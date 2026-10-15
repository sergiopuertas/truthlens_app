"""Interfaz nativa de TruthLens para la barra de menús de macOS.

La interfaz solo se puede ejecutar en macOS, donde AppKit está disponible.
"""
from __future__ import annotations

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
    import objc
except ImportError as exc:  # mensaje útil si alguien lo ejecuta fuera de macOS
    raise RuntimeError(
        "La app de barra de menús requiere macOS. Instala las dependencias con "
        "`python -m pip install -r requirements.txt`."
    ) from exc

from .app_paths import prepare_desktop_environment
from .config import load_config
from .models import NewsItem
from .report import format_report


APP_TITLE = "TruthLens"
ACCENT_COLOR = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.18, 0.28, 0.72, 1.0)
MUTED_COLOR = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.36, 0.38, 0.44, 1.0)
SURFACE_COLOR = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.97, 0.97, 0.98, 1.0)
PROVIDER_CHOICES = ["Según config.", "Groq", "Cerebras", "OpenRouter", "Ollama"]
PROVIDER_IDS = {"Groq": "groq", "Cerebras": "cerebras", "OpenRouter": "openrouter", "Ollama": "ollama"}
PROVIDER_TITLES = {value: key for key, value in PROVIDER_IDS.items()}
_menu_bar_app = None


def _call_on_main(function, *args):
    """Schedule a callback on AppKit's main queue without PyObjCTools."""
    operation = Foundation.NSBlockOperation.blockOperationWithBlock_(
        lambda: function(*args)
    )
    Foundation.NSOperationQueue.mainQueue().addOperation_(operation)


def _label(text: str, size: float = 12, bold: bool = False):
    field = AppKit.NSTextField.labelWithString_(text)
    field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
    return field


def _section_title(text: str):
    field = _label(text.upper(), 11, True)
    field.setTextColor_(ACCENT_COLOR)
    return field


def _vertical_stack(spacing: float = 7):
    stack = AppKit.NSStackView.alloc().init()
    stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
    stack.setAlignment_(AppKit.NSLayoutAttributeWidth)
    stack.setDistribution_(AppKit.NSStackViewDistributionFill)
    stack.setSpacing_(spacing)
    stack.setDetachesHiddenViews_(True)
    return stack


def _horizontal_stack(spacing: float = 8):
    stack = AppKit.NSStackView.alloc().init()
    stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
    stack.setAlignment_(AppKit.NSLayoutAttributeCenterY)
    stack.setDistribution_(AppKit.NSStackViewDistributionFill)
    stack.setSpacing_(spacing)
    stack.setDetachesHiddenViews_(True)
    return stack


def _popup(values: list[str]):
    popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSZeroRect, False)
    popup.addItemsWithTitles_(values)
    return popup


def _checkbox(title: str, state: bool = True):
    button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSZeroRect)
    button.setButtonType_(AppKit.NSButtonTypeSwitch)
    button.setTitle_(title)
    button.setState_(AppKit.NSControlStateValueOn if state else AppKit.NSControlStateValueOff)
    return button


def _redact_text(message: str) -> str:
    """Evita que informes o errores de red expongan credenciales en la interfaz."""
    for key_name in (
        "GROQ_API_KEY", "GOOGLE_FACTCHECK_API_KEY", "TAVILY_API_KEY",
        "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
    ):
        secret = os.environ.get(key_name, "").strip()
        if secret:
            message = message.replace(secret, "[clave oculta]")
    message = re.sub(r"(?i)(api[_-]?key|token|authorization|key)(\s*[=:]\s*)[^&\s,;]+", r"\1\2[oculto]", message)
    return message


def _safe_error(error: BaseException) -> str:
    return _redact_text(str(error) or error.__class__.__name__)[:1200]


def _desktop_log(message: str) -> None:
    try:
        log_path = Path.home() / "Library" / "Application Support" / "TruthLens" / "desktop.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


class TruthLensWindow(Foundation.NSObject):
    def init(self):
        self = objc.super(TruthLensWindow, self).init()
        if self is None:
            return None
        self._window = None
        self._expanded = False
        self._working = False
        self._cancel_event = threading.Event()
        self._data_dir: Path | None = None
        self._config_path: Path | None = None
        self._settings_loaded = False
        self._build_window()
        return self

    # ----- Interface -----------------------------------------------------
    def _build_window(self) -> None:
        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 620, 700),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self._window.setTitle_(APP_TITLE)
        self._window.setBackgroundColor_(SURFACE_COLOR)
        self._window.setReleasedWhenClosed_(False)
        self._window.setDelegate_(self)
        self._window.setLevel_(AppKit.NSFloatingWindowLevel)
        self._window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self._window.setMinSize_(AppKit.NSMakeSize(580, 620)        )

        content = self._window.contentView()
        root = _vertical_stack(9)
        root.setTranslatesAutoresizingMaskIntoConstraints_(False)
        content.addSubview_(root)
        AppKit.NSLayoutConstraint.activateConstraints_([
            root.leadingAnchor().constraintEqualToAnchor_constant_(content.leadingAnchor(), 28),
            root.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), -28),
            root.topAnchor().constraintEqualToAnchor_constant_(content.topAnchor(), 24),
            root.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), -24),
        ])
        self._root = root

        header = _horizontal_stack(10)
        header_text = _vertical_stack(3)
        title = _label("TruthLens", 27, True)
        title.setTextColor_(AppKit.NSColor.blackColor())
        subtitle = _label("Comprueba afirmaciones y detecta señales de desinformación.", 12)
        subtitle.setTextColor_(MUTED_COLOR)
        subtitle.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
        subtitle.setMaximumNumberOfLines_(2)
        header_text.addArrangedSubview_(title)
        header_text.addArrangedSubview_(subtitle)
        header.addArrangedSubview_(header_text)
        quit_button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 70, 28))
        quit_button.setTitle_("Cerrar")
        quit_button.setBezelStyle_(AppKit.NSBezelStyleTexturedRounded)
        quit_button.setContentTintColor_(MUTED_COLOR)
        quit_button.setTarget_(self)
        quit_button.setAction_("closeWindow:")
        header.addArrangedSubview_(quit_button)
        root.addArrangedSubview_(header)

        self._mode = AppKit.NSSegmentedControl.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 230, 28))
        self._mode.setSegmentCount_(2)
        self._mode.setLabel_forSegment_("Enlace", 0)
        self._mode.setLabel_forSegment_("Texto", 1)
        self._mode.setSelectedSegment_(0)
        self._mode.setTarget_(self)
        self._mode.setAction_("modeChanged:")
        self._mode.widthAnchor().constraintEqualToConstant_(280).setActive_(True)
        root.addArrangedSubview_(self._mode)

        self._input_label = _section_title("Contenido a comprobar")
        root.addArrangedSubview_(self._input_label)
        self._input = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 460, 66))
        self._input.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self._input.setTextColor_(AppKit.NSColor.blackColor())
        self._input.setBackgroundColor_(AppKit.NSColor.whiteColor())
        self._input.setRichText_(False)
        self._input.setVerticallyResizable_(True)
        self._input.setHorizontallyResizable_(False)
        self._input.textContainer().setContainerSize_(AppKit.NSMakeSize(0, 1000000))
        self._input.textContainer().setWidthTracksTextView_(True)
        input_scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 460, 88))
        input_scroll.setBorderType_(AppKit.NSBezelBorder)
        input_scroll.setDrawsBackground_(True)
        input_scroll.setBackgroundColor_(AppKit.NSColor.whiteColor())
        input_scroll.setHasVerticalScroller_(True)
        input_scroll.setDocumentView_(self._input)
        input_scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(88).setActive_(True)
        root.addArrangedSubview_(input_scroll)
        self._input_scroll = input_scroll
        self._input_help = _label("Admite artículos, portadas y enlaces públicos a redes sociales.", 11)
        self._input_help.setTextColor_(MUTED_COLOR)
        root.addArrangedSubview_(self._input_help)

        root.addArrangedSubview_(_section_title("Parámetros de análisis"))
        row1 = _horizontal_stack(12)
        provider_group = _vertical_stack(4)
        provider_group.addArrangedSubview_(_label("Proveedor IA", 11))
        self._provider = _popup(PROVIDER_CHOICES)
        self._provider.selectItemWithTitle_("Según config.")
        self._provider.setToolTip_("Debe haber una clave correspondiente en .env; Ollama usa un servidor local.")
        provider_group.addArrangedSubview_(self._provider)
        provider_group.widthAnchor().constraintGreaterThanOrEqualToConstant_(240).setActive_(True)
        articles_group = _vertical_stack(4)
        articles_group.addArrangedSubview_(_label("Artículos", 11))
        self._articles = _popup([str(i) for i in range(1, 11)])
        self._articles.selectItemWithTitle_("3")
        articles_group.addArrangedSubview_(self._articles)
        claims_group = _vertical_stack(4)
        claims_group.addArrangedSubview_(_label("Afirmaciones", 11))
        self._claims = _popup([str(i) for i in range(1, 6)])
        self._claims.selectItemWithTitle_("2")
        claims_group.addArrangedSubview_(self._claims)
        row1.addArrangedSubview_(provider_group)
        row1.addArrangedSubview_(articles_group)
        row1.addArrangedSubview_(claims_group)
        root.addArrangedSubview_(row1)

        row2 = _horizontal_stack(12)
        language_group = _vertical_stack(4)
        language_group.addArrangedSubview_(_label("Idioma de fact-checks", 11))
        self._language = _popup(["Español", "Inglés", "Todos"])
        language_group.addArrangedSubview_(self._language)
        confidence_group = _vertical_stack(4)
        confidence_label_row = _horizontal_stack(4)
        confidence_label_row.addArrangedSubview_(_label("Confianza mínima", 11))
        self._confidence_value = _label("70 %", 11, True)
        confidence_label_row.addArrangedSubview_(self._confidence_value)
        confidence_group.addArrangedSubview_(confidence_label_row)
        self._confidence = AppKit.NSSlider.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 210, 18))
        self._confidence.setMinValue_(0.5)
        self._confidence.setMaxValue_(0.95)
        self._confidence.setDoubleValue_(0.7)
        self._confidence.setTarget_(self)
        self._confidence.setAction_("confidenceChanged:")
        confidence_group.addArrangedSubview_(self._confidence)
        row2.addArrangedSubview_(language_group)
        row2.addArrangedSubview_(confidence_group)
        root.addArrangedSubview_(row2)

        self._risk_label = _section_title("Señales de estilo")
        root.addArrangedSubview_(self._risk_label)
        risk_row = _horizontal_stack(12)
        self._biased = _checkbox("Sesgos")
        self._fallacious = _checkbox("Falacias")
        self._hate = _checkbox("Odio")
        for check in (self._biased, self._fallacious, self._hate):
            risk_row.addArrangedSubview_(check)
        root.addArrangedSubview_(risk_row)

        self._run_button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 180, 38))
        self._run_button.setTitle_("Comprobar contenido")
        self._run_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        self._run_button.setBezelColor_(ACCENT_COLOR)
        self._run_button.setContentTintColor_(AppKit.NSColor.whiteColor())
        self._run_button.setKeyEquivalent_(u"\r")
        self._run_button.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
        self._run_button.setTarget_(self)
        self._run_button.setAction_("runCheck:")
        self._run_button.heightAnchor().constraintEqualToConstant_(38).setActive_(True)
        root.addArrangedSubview_(self._run_button)

        self._progress_row = _vertical_stack(5)
        progress_top = _horizontal_stack(8)
        self._spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 16, 16))
        self._spinner.setStyle_(AppKit.NSProgressIndicatorSpinningStyle)
        self._spinner.setIndeterminate_(True)
        self._status = _label("Preparando análisis…", 11)
        self._percent = _label("0 %", 11, True)
        progress_top.addArrangedSubview_(self._spinner)
        progress_top.addArrangedSubview_(self._status)
        spacer = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
        spacer.setContentHuggingPriority_forOrientation_(1, AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        progress_top.addArrangedSubview_(spacer)
        progress_top.addArrangedSubview_(self._percent)
        self._progress_row.addArrangedSubview_(progress_top)
        self._bar = AppKit.NSProgressIndicator.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 450, 5))
        self._bar.setStyle_(AppKit.NSProgressIndicatorBarStyle)
        self._bar.setIndeterminate_(False)
        self._bar.setMinValue_(0)
        self._bar.setMaxValue_(100)
        self._bar.setDoubleValue_(0)
        self._progress_row.addArrangedSubview_(self._bar)
        self._progress_row.setHidden_(True)
        root.addArrangedSubview_(self._progress_row)

        self._output_heading = _section_title("Resultado del análisis")
        self._output_heading.setHidden_(True)
        root.addArrangedSubview_(self._output_heading)
        self._output = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 620, 250))
        self._output.setEditable_(False)
        self._output.setSelectable_(True)
        self._output.setRichText_(False)
        self._output.setVerticallyResizable_(True)
        self._output.setHorizontallyResizable_(False)
        self._output.textContainer().setContainerSize_(AppKit.NSMakeSize(0, 1000000))
        self._output.textContainer().setWidthTracksTextView_(True)
        self._output.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        self._output.setTextColor_(AppKit.NSColor.textColor())
        self._output.setBackgroundColor_(AppKit.NSColor.whiteColor())
        self._output_scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 620, 280))
        self._output_scroll.setBorderType_(AppKit.NSBezelBorder)
        self._output_scroll.setDrawsBackground_(True)
        self._output_scroll.setBackgroundColor_(AppKit.NSColor.whiteColor())
        self._output_scroll.setHasVerticalScroller_(True)
        self._output_scroll.setDocumentView_(self._output)
        self._output_scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(280).setActive_(True)
        self._output_scroll.setHidden_(True)
        root.addArrangedSubview_(self._output_scroll)

    # ----- Window lifecycle ------------------------------------------------
    def show(self):
        if self._data_dir is None:
            self._data_dir, self._config_path = prepare_desktop_environment()
        if not self._settings_loaded and self._config_path is not None:
            self._load_saved_settings()
        self._position_window(620, 700)
        self._window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._input.window().makeFirstResponder_(self._input)

    def _load_saved_settings(self):
        """Usa la configuración persistente como valores iniciales de los controles."""
        cfg = load_config(self._config_path)
        self._provider.selectItemWithTitle_(PROVIDER_TITLES.get(cfg.llm.provider, "Según config."))
        article_count = str(max(1, min(10, cfg.scrape.max_articles)))
        claim_count = str(max(1, min(5, cfg.risk.max_claims_per_item)))
        self._articles.selectItemWithTitle_(article_count)
        self._claims.selectItemWithTitle_(claim_count)
        self._confidence.setDoubleValue_(max(0.5, min(0.95, cfg.risk.verdict_min_confidence)))
        self._confidence_value.setStringValue_(f"{round(self._confidence.doubleValue() * 100)} %")
        lang = {"en": "Inglés", "": "Todos"}.get(cfg.factcheck.language, "Español")
        self._language.selectItemWithTitle_(lang)
        style_ids = {v.id for v in cfg.risk.style_vectors}
        for vector, check in (("biased", self._biased), ("fallacious", self._fallacious), ("hate", self._hate)):
            check.setState_(AppKit.NSControlStateValueOn if vector in style_ids else AppKit.NSControlStateValueOff)
        self._settings_loaded = True

    def _position_window(self, width: float, height: float):
        screen = AppKit.NSScreen.mainScreen()
        visible = screen.visibleFrame() if screen else AppKit.NSMakeRect(0, 0, 1200, 800)
        height = min(height, max(560, visible.size.height - 24))
        width = min(width, max(500, visible.size.width - 24))
        x = visible.origin.x + visible.size.width - width - 12
        top = visible.origin.y + visible.size.height - 12
        y = max(visible.origin.y + 4, top - height)
        self._window.setFrame_display_animate_(AppKit.NSMakeRect(x, y, width, height), True, False)

    def _expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._output_heading.setHidden_(False)
        self._output_scroll.setHidden_(False)
        self._position_window(760, 860)

    # ----- Events ----------------------------------------------------------
    def quitApp_(self, _sender: Any):
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def closeWindow_(self, _sender: Any):
        self._window.performClose_(None)

    def windowWillClose_(self, _notification: Any):
        self._cancel_analysis()

    def _cancel_analysis(self):
        self._cancel_event.set()
        self._working = False
        self._spinner.stopAnimation_(None)
        self._run_button.setEnabled_(True)
        self._progress_row.setHidden_(True)
        self._output_heading.setHidden_(True)
        self._output_scroll.setHidden_(True)
        self._output.setString_("")
        self._input.setString_("")
        self._expanded = False

    def modeChanged_(self, _sender: Any):
        if self._mode.selectedSegment() == 0:
            self._input_label.setStringValue_("URL de la noticia o publicación")
            self._input_help.setStringValue_("Admite artículos, portadas y enlaces públicos a redes sociales.")
        else:
            self._input_label.setStringValue_("Texto o afirmación que quieres comprobar")
            self._input_help.setStringValue_("Pega el contenido completo para analizarlo; no se enviará ninguna URL a extraer.")

    def confidenceChanged_(self, _sender: Any):
        self._confidence_value.setStringValue_(f"{round(self._confidence.doubleValue() * 100)} %")

    def runCheck_(self, _sender: Any):
        if self._working:
            return
        _desktop_log("runCheck: iniciado")
        value = self._input.string().strip()
        is_url = self._mode.selectedSegment() == 0
        if not value:
            self._show_input_error("Introduce una URL o pega el texto que quieres comprobar.")
            return
        if is_url:
            parsed = urlparse(value if "://" in value else f"https://{value}")
            if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
                self._show_input_error("La URL no parece válida. Pega un enlace completo, por ejemplo https://medio.es/noticia")
                return
            value = parsed.geturl()
        self._working = True
        self._cancel_event.clear()
        self._run_button.setEnabled_(False)
        self._progress_row.setHidden_(False)
        self._spinner.startAnimation_(None)
        self._bar.setDoubleValue_(0)
        self._percent.setStringValue_("0 %")
        self._status.setStringValue_("Preparando análisis…")
        self._output.setString_("")
        if self._expanded:
            self._output_heading.setHidden_(True)
            self._output_scroll.setHidden_(True)
            self._expanded = False
            self._position_window(520, 605)
        options = {
            "provider": PROVIDER_IDS.get(self._provider.titleOfSelectedItem()),
            "articles": int(self._articles.titleOfSelectedItem()),
            "claims": int(self._claims.titleOfSelectedItem()),
            "confidence": float(self._confidence.doubleValue()),
            "language": {"Español": "es", "Inglés": "en", "Todos": ""}.get(
                self._language.titleOfSelectedItem(), "es"
            ),
            "style_vectors": [
                name for name, check in (("biased", self._biased), ("fallacious", self._fallacious), ("hate", self._hate))
                if check.state() == AppKit.NSControlStateValueOn
            ],
        }
        thread = threading.Thread(target=self._analyze_worker, args=(value, is_url, options), daemon=True)
        thread.start()
        _desktop_log(f"runCheck: worker lanzado (url={is_url})")

    def _show_input_error(self, message: str):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Falta información")
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("Entendido")
        alert.runModal()

    def _progress_update(self, percent: int, message: str):
        _call_on_main(self._set_progress, percent, message)

    def _set_progress(self, percent: int, message: str):
        if not self._working:
            return
        value = max(float(self._bar.doubleValue()), min(99.0, float(percent)))
        self._bar.setDoubleValue_(value)
        self._percent.setStringValue_(f"{int(value)} %")
        self._status.setStringValue_(message)

    def _analyze_worker(self, value: str, is_url: bool, options: dict[str, Any]):
        try:
            _desktop_log("worker: empezando")
            from .main import build_pipeline
            _desktop_log("worker: imports cargados")

            if self._config_path is None or self._data_dir is None:
                raise RuntimeError("No se pudo preparar la configuración de usuario.")
            self._progress_update(2, "Cargando configuración…")
            cfg = load_config(self._config_path)
            _desktop_log(f"worker: config cargada; proveedor={cfg.llm.provider}")
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

            self._progress_update(5, "Preparando proveedor de IA…")
            pipeline = build_pipeline(cfg, warmup=False, progress_callback=self._progress_update)
            _desktop_log("worker: pipeline creado")
            self._progress_update(10, "Proveedor listo; descargando contenido…")
            if is_url:
                _desktop_log("worker: iniciando scraping")
                reports = pipeline.check_url(value)
            else:
                self._progress_update(12, "Analizando el texto proporcionado…")
                _desktop_log("worker: iniciando análisis de texto")
                reports = [pipeline.analyze(NewsItem(headline=value))]
                self._progress_update(100, "Análisis completado")
            report = _redact_text(format_report(reports))
            _desktop_log("worker: análisis terminado; enviando resultado")
            _call_on_main(self._analysis_finished, report, None)
        except Exception as exc:
            _desktop_log(f"worker: ERROR {type(exc).__name__}: {_safe_error(exc)}")
            _desktop_log(traceback.format_exc())
            _call_on_main(self._analysis_finished, "", _safe_error(exc))

    def _analysis_finished(self, report: str, error: str | None):
        _desktop_log(f"ui: resultado recibido (error={bool(error)})")
        if not self._working:
            return
        self._working = False
        self._run_button.setEnabled_(True)
        self._spinner.stopAnimation_(None)
        self._bar.setDoubleValue_(100 if not error else self._bar.doubleValue())
        self._percent.setStringValue_("100 %" if not error else self._percent.stringValue())
        self._status.setStringValue_("Análisis completado" if not error else "No se pudo completar")
        self._output.setString_(report if not error else f"No se pudo completar el análisis\n\n{error}")
        self._expand()
        self._output.scrollRangeToVisible_(Foundation.NSMakeRange(len(self._output.string()), 0))


class TruthLensStatusItemView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(TruthLensStatusItemView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._target = None
        self._context_menu = None
        self.setToolTip_("TruthLens — comprobar contenido")
        return self

    def setTarget_(self, target):
        self._target = target

    def drawRect_(self, _rect):
        title = Foundation.NSString.stringWithString_("TL")
        attributes = {
            AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(12),
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(),
        }
        title.drawInRect_withAttributes_(self.bounds(), attributes)

    def mouseDown_(self, _event):
        if self._target is not None:
            self._target.openTruthLens_(self)

    def rightMouseDown_(self, event):
        if self._context_menu is None:
            self._context_menu = AppKit.NSMenu.alloc().initWithTitle_("TruthLens")
            exit_item = self._context_menu.addItemWithTitle_action_keyEquivalent_(
                "Salir", "quitApplication:", ""
            )
            exit_item.setTarget_(self._target)
        AppKit.NSMenu.popUpContextMenu_withEvent_forView_(self._context_menu, event, self)


class TruthLensMenuBarApp(Foundation.NSObject):
    """Un NSStatusItem real: un clic sobre TL abre/cierra la ventana compacta."""

    def init(self):
        self = objc.super(TruthLensMenuBarApp, self).init()
        if self is None:
            return None
        self._window_controller = None
        self._status_item = None
        return self

    def start(self):
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        signal.signal(signal.SIGINT, self._signal_received)
        signal.signal(signal.SIGTERM, self._signal_received)
        self._status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self._status_view = TruthLensStatusItemView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 30, 22)
        )
        self._status_view.setTarget_(self)
        self._status_item.setView_(self._status_view)
        app.run()

    def _signal_received(self, _signum: int, _frame: Any):
        _call_on_main(self.stop)

    def stop(self):
        if self._status_item is not None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        AppKit.NSApplication.sharedApplication().stop_(None)

    def quitApplication_(self, _sender=None):
        if self._window_controller is not None:
            self._window_controller._cancel_analysis()
            self._window_controller._window.orderOut_(None)
        self.stop()
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def openTruthLens_(self, _sender=None):
        if self._window_controller is None:
            self._window_controller = TruthLensWindow.alloc().init()
        window = self._window_controller._window
        if window.isVisible():
            window.orderOut_(None)
        else:
            self._window_controller.show()


def run() -> int:
    if sys.platform != "darwin":
        print("TruthLens Desktop está diseñado para macOS.", file=sys.stderr)
        return 2
    global _menu_bar_app
    _menu_bar_app = TruthLensMenuBarApp.alloc().init()
    _menu_bar_app.start()
    return 0
