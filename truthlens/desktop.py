"""TruthLens macOS menu-bar app.

The native macOS shell (status item/window) stays in Python/AppKit. The actual
application interface is HTML/CSS/JavaScript rendered by WKWebView, which makes
it easier to maintain and gives the UI modern layout and interaction patterns.
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import AppKit
    import Foundation
    import WebKit
    import objc
except ImportError as exc:  # A useful error if run outside the macOS environment.
    raise RuntimeError(
        "TruthLens Desktop requiere macOS y PyObjC/WebKit. "
        "Ejecuta `python -m pip install -r requirements.txt`."
    ) from exc

from .app_paths import prepare_desktop_environment
from .config import load_config
from .models import NewsItem
from .report import format_report

APP_TITLE = "TruthLens"
PROVIDER_IDS = {"Groq": "groq", "Cerebras": "cerebras", "OpenRouter": "openrouter", "Ollama": "ollama"}
PROVIDER_TITLES = {value: key for key, value in PROVIDER_IDS.items()}
ALLOWED_VECTORS = {"biased", "fallacious", "hate"}
_menu_bar_app = None


def _call_on_main(function, *args) -> None:
    """Schedule a callback on AppKit's main queue."""
    operation = Foundation.NSBlockOperation.blockOperationWithBlock_(
        lambda: function(*args)
    )
    Foundation.NSOperationQueue.mainQueue().addOperation_(operation)


def _redact_text(message: str) -> str:
    """Avoid exposing credentials in the UI or log output."""
    for key_name in (
        "GROQ_API_KEY", "GOOGLE_FACTCHECK_API_KEY", "TAVILY_API_KEY",
        "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
    ):
        secret = os.environ.get(key_name, "").strip()
        if secret:
            message = message.replace(secret, "[clave oculta]")
    message = re.sub(
        r"(?i)(api[_-]?key|token|authorization|key)(\s*[=:]\s*)[^&\s,;]+",
        r"\1\2[oculto]",
        message,
    )
    return message


def _safe_error(error: BaseException) -> str:
    return _redact_text(str(error) or error.__class__.__name__)[:1600]


def _bridge_to_python(value: Any) -> Any:
    """Convert WebKit's Foundation collections into ordinary Python values."""
    if isinstance(value, dict):
        return {str(key): _bridge_to_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bridge_to_python(item) for item in value]
    if hasattr(value, "allKeys") and hasattr(value, "objectForKey_"):
        return {
            str(key): _bridge_to_python(value.objectForKey_(key))
            for key in value.allKeys()
        }
    if hasattr(value, "count") and hasattr(value, "objectAtIndex_"):
        try:
            return [_bridge_to_python(value.objectAtIndex_(index)) for index in range(int(value.count()))]
        except Exception:
            pass
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _desktop_log(message: str) -> None:
    try:
        log_path = Path.home() / "Library" / "Application Support" / "TruthLens" / "desktop.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        # Logging must never prevent the application from starting.
        pass


def _find_ui_file() -> Path:
    """Locate the bundled HTML both in a source checkout and in a py2app bundle."""
    executable = Path(sys.executable).resolve()
    resources = executable.parent.parent / "Resources"
    package_dir = Path(__file__).resolve().parent
    candidates = (
        package_dir / "web_ui" / "index.html",
        resources / "web_ui" / "index.html",
        resources / "truthlens" / "web_ui" / "index.html",
        resources / "lib" / "python" / "truthlens" / "web_ui" / "index.html",
        Path.cwd() / "truthlens" / "web_ui" / "index.html",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = "\n".join(str(path) for path in candidates)
    raise FileNotFoundError(f"No se encuentra la interfaz HTML de TruthLens. Rutas comprobadas:\n{searched}")


class TruthLensWindow(Foundation.NSObject):
    """WKWebView window and the small bridge between JavaScript and Python."""

    def init(self):
        self = objc.super(TruthLensWindow, self).init()
        if self is None:
            return None
        self._window = None
        self._webview = None
        self._working = False
        self._run_id = 0
        self._data_dir: Path | None = None
        self._config_path: Path | None = None
        self._ui_file: Path | None = None
        self._build_window()
        return self

    def _build_window(self) -> None:
        _desktop_log("ui: creando ventana WKWebView")
        self._data_dir, self._config_path = prepare_desktop_environment()
        self._ui_file = _find_ui_file()
        _desktop_log(f"ui: html={self._ui_file}")

        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 660, 480),
            (
                AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable
                | AppKit.NSWindowStyleMaskResizable
            ),
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self._window.setTitle_(APP_TITLE)
        self._window.setReleasedWhenClosed_(False)
        self._window.setDelegate_(self)
        self._window.setMinSize_(AppKit.NSMakeSize(560, 400))
        self._window.setLevel_(AppKit.NSFloatingWindowLevel)
        self._window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        content = self._window.contentView()
        web_configuration = WebKit.WKWebViewConfiguration.alloc().init()
        user_content_controller = WebKit.WKUserContentController.alloc().init()
        user_content_controller.addScriptMessageHandler_name_(self, "truthlens")
        web_configuration.setUserContentController_(user_content_controller)
        self._webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            content.bounds(), web_configuration
        )
        self._webview.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        content.addSubview_(self._webview)

        html_url = Foundation.NSURL.fileURLWithPath_(str(self._ui_file))
        read_access_url = Foundation.NSURL.fileURLWithPath_(str(self._ui_file.parent))
        self._webview.loadFileURL_allowingReadAccessToURL_(html_url, read_access_url)

    # ----- Window lifecycle -------------------------------------------------
    def show(self) -> None:
        if self._config_path is None or self._data_dir is None:
            self._data_dir, self._config_path = prepare_desktop_environment()
        self._position_window(660, 480)
        self._window.makeKeyAndOrderFront_(None)
        self._window.deminiaturize_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._webview.window().makeFirstResponder_(self._webview)

    def _position_window(self, width: float, height: float) -> None:
        screen = AppKit.NSScreen.mainScreen()
        visible = screen.visibleFrame() if screen else AppKit.NSMakeRect(0, 0, 1200, 800)
        width = min(width, max(760, visible.size.width - 24))
        height = min(height, max(620, visible.size.height - 24))
        x = visible.origin.x + visible.size.width - width - 12
        y = visible.origin.y + visible.size.height - height - 12
        self._window.setFrame_display_animate_(AppKit.NSMakeRect(x, y, width, height), True, False)

    def windowWillClose_(self, _notification: Any) -> None:
        self._invalidate_current_run()

    def closeWindow_(self, _sender: Any = None) -> None:
        self._window.performClose_(None)

    def quitApp_(self, _sender: Any = None) -> None:
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def _invalidate_current_run(self) -> None:
        self._run_id += 1
        self._working = False
        self._call_js("setBusy", False)

    # ----- JavaScript bridge ------------------------------------------------
    @objc.typedSelector(b"v@:@@")
    def userContentController_didReceiveScriptMessage_(self, _controller, message) -> None:
        """WKScriptMessageHandler callback for window.webkit.messageHandlers.truthlens."""
        try:
            body = _bridge_to_python(message.body())
            if not isinstance(body, dict):
                _desktop_log("bridge: mensaje ignorado (no es un objeto)")
                return
            action = str(body.get("action", ""))
            if action == "ready":
                self._send_initial_config()
            elif action == "analyze":
                self._start_analysis(body)
            elif action == "resize":
                mode = str(body.get("mode", "compact"))
                if mode == "results":
                    self._position_window(1000, 790)
                else:
                    self._position_window(660, 480)
            elif action == "open_url":
                self._open_external_url(str(body.get("url", "")))
            elif action == "quit":
                self.quitApp_()
            else:
                _desktop_log(f"bridge: acción desconocida={action[:80]}")
        except Exception:
            _desktop_log("bridge: error al procesar mensaje\n" + traceback.format_exc())
            self._call_js("showFatalMessage", "No se pudo procesar la acción de la interfaz.")

    def _call_js(self, function_name: str, *arguments: Any) -> None:
        if self._webview is None:
            return
        # Only internal, fixed function names are passed here; values are JSON encoded.
        encoded = ",".join(json.dumps(arg, ensure_ascii=False, default=str).replace("</", "<\\/") for arg in arguments)
        script = f"window.TruthLens && window.TruthLens.{function_name}({encoded});"
        try:
            self._webview.evaluateJavaScript_completionHandler_(script, None)
        except Exception as exc:
            _desktop_log(f"bridge: no se pudo actualizar la interfaz: {_safe_error(exc)}")

    def _send_initial_config(self) -> None:
        if self._config_path is None:
            self._call_js("showFatalMessage", "No se ha podido localizar la configuración de usuario.")
            return
        try:
            cfg = load_config(self._config_path)
            settings = {
                "provider": cfg.llm.provider,
                "articles": max(1, min(10, int(cfg.scrape.max_articles))),
                "claims": max(1, min(5, int(cfg.risk.max_claims_per_item))),
                "confidence": max(0.5, min(0.95, float(cfg.risk.verdict_min_confidence))),
                "language": cfg.factcheck.language,
                "style_vectors": [v.id for v in cfg.risk.style_vectors if v.id in ALLOWED_VECTORS],
            }
            self._call_js("setInitialConfig", settings)
            _desktop_log(f"ui: configuración inicial cargada (provider={cfg.llm.provider})")
        except Exception as exc:
            _desktop_log(f"ui: error cargando configuración: {_safe_error(exc)}")
            self._call_js("showFatalMessage", f"No se pudo cargar la configuración: {_safe_error(exc)}")

    def _open_external_url(self, raw_url: str) -> None:
        parsed = urlparse(raw_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            _desktop_log("bridge: se rechazó un enlace externo no permitido")
            return
        try:
            AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_(parsed.geturl()))
        except Exception as exc:
            _desktop_log(f"bridge: error abriendo enlace: {_safe_error(exc)}")

    # ----- Analysis ---------------------------------------------------------
    def _start_analysis(self, body: dict[str, Any]) -> None:
        if self._working:
            return
        value = str(body.get("value", "")).strip()
        is_url = bool(body.get("isUrl", True))
        if not value:
            self._call_js("showInputError", "Introduce una URL o pega el texto que quieres comprobar.")
            return
        if len(value) > 100_000:
            self._call_js("showInputError", "El texto es demasiado largo (máximo 100.000 caracteres).")
            return
        if is_url:
            parsed = urlparse(value if "://" in value else f"https://{value}")
            if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
                self._call_js(
                    "showInputError",
                    "La URL no parece válida. Pega un enlace completo, por ejemplo https://medio.es/noticia.",
                )
                return
            value = parsed.geturl()

        raw_options = body.get("options") if isinstance(body.get("options"), dict) else {}
        provider = str(raw_options.get("provider", ""))
        options = {
            "provider": provider if provider in set(PROVIDER_IDS.values()) else None,
            "articles": self._bounded_int(raw_options.get("articles", 3), 3, 1, 10),
            "claims": self._bounded_int(raw_options.get("claims", 2), 2, 1, 5),
            "confidence": self._bounded_float(raw_options.get("confidence", 0.7), 0.7, 0.5, 0.95),
            "language": raw_options.get("language", "es") if raw_options.get("language", "es") in ("es", "en", "") else "es",
            "style_vectors": [
                item for item in raw_options.get("style_vectors", [])
                if isinstance(item, str) and item in ALLOWED_VECTORS
            ] if isinstance(raw_options.get("style_vectors", []), list) else [],
        }
        self._working = True
        self._run_id += 1
        run_id = self._run_id
        self._call_js("clearInputError")
        self._call_js("clearResults")
        self._call_js("setBusy", True)
        self._call_js("setProgress", 0, "Preparando análisis…")
        _desktop_log(f"análisis: iniciado run={run_id}; url={is_url}; provider={options['provider'] or 'config'}")
        threading.Thread(
            target=self._analyze_worker,
            args=(run_id, value, is_url, options),
            daemon=True,
            name=f"TruthLensAnalysis-{run_id}",
        ).start()

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(value)))
        except (TypeError, ValueError, OverflowError):
            return default

    def _progress_update(self, run_id: int, percent: int, message: str) -> None:
        _call_on_main(self._set_progress, run_id, percent, message)

    def _set_progress(self, run_id: int, percent: int, message: str) -> None:
        if not self._working or run_id != self._run_id:
            return
        self._call_js("setProgress", max(0, min(99, int(percent))), str(message)[:240])

    def _analyze_worker(self, run_id: int, value: str, is_url: bool, options: dict[str, Any]) -> None:
        try:
            _desktop_log(f"worker {run_id}: comenzando")
            from .main import build_pipeline

            if self._config_path is None or self._data_dir is None:
                raise RuntimeError("No se pudo preparar la configuración de usuario.")
            self._progress_update(run_id, 2, "Cargando configuración…")
            cfg = load_config(self._config_path)
            if options["provider"]:
                cfg.llm.provider = options["provider"]
            cfg.scrape.max_articles = options["articles"]
            cfg.risk.max_claims_per_item = options["claims"]
            cfg.risk.verdict_min_confidence = options["confidence"]
            cfg.factcheck.language = options["language"]
            selected = set(options["style_vectors"])
            cfg.risk.style_vectors = [v for v in cfg.risk.style_vectors if v.id in selected]
            if not Path(cfg.low_credibility_csv).is_absolute():
                cfg.low_credibility_csv = str(self._data_dir / cfg.low_credibility_csv)

            self._progress_update(run_id, 5, "Preparando proveedor de IA…")
            pipeline = build_pipeline(
                cfg,
                warmup=False,
                progress_callback=lambda percent, message: self._progress_update(run_id, percent, message),
            )
            self._progress_update(run_id, 10, "Proveedor listo; analizando contenido…")
            if is_url:
                reports = pipeline.check_url(value)
            else:
                self._progress_update(run_id, 12, "Analizando el texto proporcionado…")
                reports = [pipeline.analyze(NewsItem(headline=value))]
                self._progress_update(run_id, 95, "Preparando el informe…")

            report_items = []
            for report in reports:
                if hasattr(report, "model_dump"):
                    report_items.append(report.model_dump(mode="json"))
                elif hasattr(report, "dict"):
                    report_items.append(report.dict())
                else:
                    raise TypeError("El motor ha devuelto un informe con un formato no reconocido.")
            formatted = _redact_text(format_report(reports))
            _desktop_log(f"worker {run_id}: análisis terminado; elementos={len(report_items)}")
            _call_on_main(self._analysis_finished, run_id, report_items, formatted, None)
        except Exception as exc:
            _desktop_log(f"worker {run_id}: ERROR {type(exc).__name__}: {_safe_error(exc)}")
            _desktop_log(traceback.format_exc())
            _call_on_main(self._analysis_finished, run_id, [], "", _safe_error(exc))

    def _analysis_finished(
        self,
        run_id: int,
        report_items: list[dict[str, Any]],
        formatted: str,
        error: str | None,
    ) -> None:
        if not self._working or run_id != self._run_id:
            return
        self._working = False
        if error:
            self._call_js("setBusy", False)
            self._call_js("setProgress", 0, "No se pudo completar el análisis")
            self._call_js("showAnalysisError", error)
            _desktop_log(f"ui: error presentado run={run_id}")
            return
        self._call_js("setProgress", 100, "Análisis completado")
        self._call_js("setBusy", False)
        self._call_js("showResults", {"reports": report_items, "formatted": formatted})
        _desktop_log(f"ui: informe presentado run={run_id}")


class TruthLensStatusItemView(AppKit.NSView):
    """Clickable TL icon in the macOS menu bar."""

    def initWithFrame_(self, frame):
        self = objc.super(TruthLensStatusItemView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._target = None
        self._context_menu = None
        self.setToolTip_("TruthLens — comprobar contenido")
        return self

    def setTarget_(self, target) -> None:
        self._target = target

    def drawRect_(self, _rect) -> None:
        title = Foundation.NSString.stringWithString_("TL")
        attributes = {
            AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(12),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(),
        }
        title.drawInRect_withAttributes_(self.bounds(), attributes)

    def mouseDown_(self, _event) -> None:
        if self._target is not None:
            self._target.openTruthLens_(self)

    def rightMouseDown_(self, event) -> None:
        if self._context_menu is None:
            self._context_menu = AppKit.NSMenu.alloc().initWithTitle_("TruthLens")
            exit_item = self._context_menu.addItemWithTitle_action_keyEquivalent_("Salir", "quitApplication:", "")
            exit_item.setTarget_(self._target)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self._context_menu, event, self)


class TruthLensMenuBarApp(Foundation.NSObject):
    """Owns the status item and shows/hides the web-based window."""

    def init(self):
        self = objc.super(TruthLensMenuBarApp, self).init()
        if self is None:
            return None
        self._window_controller = None
        self._status_item = None
        self._status_view = None
        return self

    def start(self) -> None:
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        signal.signal(signal.SIGINT, self._signal_received)
        signal.signal(signal.SIGTERM, self._signal_received)
        self._status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self._status_view = TruthLensStatusItemView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 30, 22))
        self._status_view.setTarget_(self)
        self._status_item.setView_(self._status_view)
        _desktop_log("app: iniciado; esperando clic en el icono TL")
        app.run()

    def _signal_received(self, _signum: int, _frame: Any) -> None:
        _call_on_main(self.stop)

    def stop(self) -> None:
        if self._status_item is not None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        AppKit.NSApplication.sharedApplication().stop_(None)

    def quitApplication_(self, _sender=None) -> None:
        if self._window_controller is not None:
            self._window_controller._invalidate_current_run()
            self._window_controller._window.orderOut_(None)
        self.stop()
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def openTruthLens_(self, _sender=None) -> None:
        try:
            if self._window_controller is None:
                self._window_controller = TruthLensWindow.alloc().init()
            window = self._window_controller._window
            if window.isVisible():
                window.orderOut_(None)
            else:
                self._window_controller.show()
        except Exception as exc:
            _desktop_log(f"app: no se pudo abrir la ventana: {_safe_error(exc)}")
            _desktop_log(traceback.format_exc())
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("No se pudo abrir TruthLens")
            alert.setInformativeText_(
                f"{_safe_error(exc)}\n\nRegistro de diagnóstico: "
                "~/Library/Application Support/TruthLens/desktop.log"
            )
            alert.addButtonWithTitle_("Aceptar")
            alert.runModal()


def run() -> int:
    """Start the app as a macOS menu-bar accessory."""
    if sys.platform != "darwin":
        print("TruthLens Desktop está diseñado para macOS.", file=sys.stderr)
        return 2
    global _menu_bar_app
    try:
        prepare_desktop_environment()
        _menu_bar_app = TruthLensMenuBarApp.alloc().init()
        _menu_bar_app.start()
        return 0
    except Exception:
        _desktop_log("app: error fatal al iniciar\n" + traceback.format_exc())
        raise
