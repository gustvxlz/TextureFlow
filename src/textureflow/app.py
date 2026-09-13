from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from textureflow import __version__
from textureflow.core import (
    AppConfig, TextureFlowError, TexturePipeline, install_texture_hook,
    inspect_texture_hook, is_game_running, load_config, remove_texture_hook,
    runtime_injection_enabled, save_config, set_runtime_injection, wait_until_stable,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "state" / "config.json"
PROFILES = (
    "DirectX 9 - Resident Evil 4 (2005)",
    "DirectX 8 - Postal 2",
    "DirectX 9/11 - Generico",
)
PROFILE_ALIASES = {
    "RE4 2005 / Ultimate HD (D3D9)": PROFILES[0],
    "Postal 2 (D3D8 nativo)": PROFILES[1],
    "Generico D3D9/D3D11": PROFILES[2],
}


class TextureFlowApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"TextureFlow {__version__}")
        self.geometry("1040x760")
        self.minsize(900, 680)
        self.config_data = load_config(CONFIG_PATH)
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.processed = 0
        self.skipped = 0
        self.errors = 0
        self.seen_session: dict[str, tuple[tuple[int, int], str]] = {}
        self._last_runtime_poll = 0.0
        self._build_ui()
        self._load_values()
        self.after(100, self._drain_messages)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        background = "#0b0f14"
        surface = "#121821"
        elevated = "#18212c"
        border = "#273342"
        text = "#edf3f8"
        muted = "#91a0af"
        accent = "#36a3ff"
        accent_hover = "#61b6ff"
        danger = "#dd5b62"
        self.configure(background=background)
        self.option_add("*Font", ("Segoe UI", 10))
        self.option_add("*TCombobox*Listbox.background", elevated)
        self.option_add("*TCombobox*Listbox.foreground", text)
        self.option_add("*TCombobox*Listbox.selectBackground", accent)
        self.option_add("*TCombobox*Listbox.selectForeground", "#07111b")

        style.configure("TFrame", background=background)
        style.configure("TLabel", background=background, foreground=text)
        style.configure("Muted.TLabel", foreground=muted)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground=text)
        style.configure("Version.TLabel", background=elevated, foreground=accent,
                        padding=(10, 5), font=("Segoe UI Semibold", 9))
        style.configure("Card.TLabelframe", background=surface, bordercolor=border,
                        lightcolor=border, darkcolor=border, relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=surface, foreground=text,
                        font=("Segoe UI Semibold", 11))
        style.configure("Card.TFrame", background=surface)
        style.configure("Card.TLabel", background=surface, foreground=text)
        style.configure("CardMuted.TLabel", background=surface, foreground=muted)
        style.configure("Info.TLabel", background=elevated, foreground="#b9dcff", padding=12)
        style.configure("Status.TLabel", background=elevated, foreground="#77d6a0",
                        padding=(10, 5), font=("Segoe UI Semibold", 9))
        style.configure("TButton", background=elevated, foreground=text, bordercolor=border,
                        padding=(12, 8), focusthickness=0)
        style.map("TButton", background=[("active", "#223043"), ("disabled", "#111720")],
                  foreground=[("disabled", "#5f6b77")])
        style.configure("Accent.TButton", background=accent, foreground="#06111c",
                        bordercolor=accent, font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", accent_hover), ("disabled", "#245276")])
        style.configure("Danger.TButton", foreground="#ff9da2")
        style.map("Danger.TButton", background=[("active", "#44262d")])
        style.configure("TEntry", fieldbackground=elevated, foreground=text,
                        insertcolor=text, bordercolor=border, padding=7)
        style.configure("TCombobox", fieldbackground=elevated, background=elevated,
                        foreground=text, arrowcolor=muted, bordercolor=border, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", elevated)],
                  foreground=[("readonly", text)], selectbackground=[("readonly", elevated)],
                  selectforeground=[("readonly", text)])
        style.configure("TCheckbutton", background=surface, foreground=text, padding=(0, 3))
        style.map("TCheckbutton", background=[("active", surface)], foreground=[("active", text)])
        style.configure("TNotebook", background=background, borderwidth=0)
        style.configure("TNotebook.Tab", background=surface, foreground=muted,
                        padding=(18, 10), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", elevated), ("active", "#151d27")],
                  foreground=[("selected", text), ("active", text)])

        outer = ttk.Frame(self, padding=(22, 18, 22, 20))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        heading = ttk.Frame(header)
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, text="TextureFlow", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            heading,
            text="Super-resolucao neural de texturas com cache local e injecao reversivel.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(header, text=f"ALPHA  {__version__}", style="Version.TLabel").pack(side="right")

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        setup = ttk.Frame(tabs, padding=(2, 16, 2, 2))
        live = ttk.Frame(tabs, padding=(2, 16, 2, 2))
        tabs.add(setup, text="CONFIGURAR")
        tabs.add(live, text="PROCESSAMENTO")

        setup.columnconfigure(1, weight=1)
        game_box = ttk.LabelFrame(setup, text=" Jogo e API grafica ", style="Card.TLabelframe", padding=14)
        game_box.grid(row=0, column=0, columnspan=3, sticky="ew")
        game_box.columnconfigure(1, weight=1)
        ttk.Label(game_box, text="Modo DirectX", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=6)
        self.profile_var = tk.StringVar()
        ttk.Combobox(game_box, textvariable=self.profile_var, values=PROFILES, state="readonly").grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(game_box, text="Executavel", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 14), pady=6)
        self.exe_var = tk.StringVar()
        ttk.Entry(game_box, textvariable=self.exe_var).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(game_box, text="Selecionar...", command=self._browse_exe).grid(
            row=1, column=2, padx=(8, 0), pady=6)

        box = ttk.LabelFrame(setup, text=" Qualidade e memoria ", style="Card.TLabelframe", padding=14)
        box.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        box.columnconfigure(1, weight=1)
        self.scale_var = tk.IntVar(value=2)  # Kept for backward-compatible config files.
        self.minimum_resolution_var = tk.IntVar(value=1024)
        ttk.Label(box, text="Resolucao minima", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            box, textvariable=self.minimum_resolution_var,
            values=(512, 1024, 2048, 4096), width=10, state="readonly",
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(box, text="px no lado maior; a proporcao original e mantida", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(8, 0))

        self.vram_budget_var = tk.IntVar(value=2048)
        ttk.Label(box, text="Limite de VRAM", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            box, textvariable=self.vram_budget_var,
            values=(512, 1024, 2048, 4096, 8192), width=10, state="readonly",
        ).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Label(box, text="MB; o DirectX 8 libera primeiro as menos usadas", style="CardMuted.TLabel").grid(
            row=1, column=2, sticky="w", padx=(8, 0), pady=(10, 0))

        ttk.Label(box, text="Modelo neural", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(box, text="4xNomos8kSC  |  Alta qualidade", style="CardMuted.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(10, 0))

        self.anti_stretch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box,
            text="Reforco anti-esticamento para texturas pequenas",
            variable=self.anti_stretch_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.appearance_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box,
            text="Preservar cor e iluminacao da textura original (recomendado)",
            variable=self.appearance_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.sensitive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box,
            text="Proteger letras, interface e rostos contra deformacao (recomendado)",
            variable=self.sensitive_var,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.defer_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box,
            text="Modo sem pop-in: preparar durante o jogo e ativar na proxima abertura",
            variable=self.defer_var,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.safe_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Modo seguro: ignorar normal maps, mascaras e texturas suspeitas", variable=self.safe_var).grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.alpha_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Ignorar qualquer textura com transparencia relevante", variable=self.alpha_var).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        controls = ttk.Frame(setup)
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Button(controls, text="Instalar no jogo", command=self._install,
                   style="Accent.TButton").pack(side="left")
        ttk.Button(controls, text="Salvar", command=self._save_from_ui).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Verificar", command=self._diagnose).pack(side="left")
        ttk.Button(controls, text="Abrir cache", command=self._open_tt).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Remover", command=self._remove,
                   style="Danger.TButton").pack(side="right")

        self.profile_help_var = tk.StringVar()
        ttk.Label(setup, textvariable=self.profile_help_var, wraplength=930, style="Info.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.profile_var.trace_add("write", self._profile_changed)

        top = ttk.Frame(live, style="Card.TFrame", padding=14)
        top.pack(fill="x")
        self.start_button = ttk.Button(top, text="Iniciar monitoramento", command=self._start,
                                       style="Accent.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(top, text="Parar", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(top, text="Executar uma varredura", command=self._scan_once).pack(side="left")
        self.status_var = tk.StringVar(value="Parado")
        ttk.Label(top, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        runtime = ttk.LabelFrame(live, text=" Controle em tempo real ", style="Card.TLabelframe", padding=14)
        runtime.pack(fill="x", pady=(14, 0))
        self.toggle_button = ttk.Button(runtime, text="Desativar aprimoradas (HOME)", command=self._toggle_injection)
        self.toggle_button.pack(side="left")
        self.injection_var = tk.StringVar(value="")
        ttk.Label(runtime, textvariable=self.injection_var, style="CardMuted.TLabel").pack(side="left", padx=(12, 0))

        self.stats_var = tk.StringVar(value="Processadas: 0  |  Ignoradas: 0  |  Erros: 0")
        ttk.Label(live, textvariable=self.stats_var, style="Muted.TLabel",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(14, 7))
        log_frame = ttk.Frame(live, style="Card.TFrame", padding=1)
        log_frame.pack(fill="both", expand=True)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame, height=19, wrap="word", state="disabled", font=("Cascadia Mono", 9),
            background="#0e141c", foreground="#cbd6e1", insertbackground=text,
            selectbackground="#245a83", relief="flat", borderwidth=0, padx=12, pady=10,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _load_values(self) -> None:
        profile = PROFILE_ALIASES.get(self.config_data.profile, self.config_data.profile)
        self.profile_var.set(profile if profile in PROFILES else PROFILES[0])
        self.exe_var.set(self.config_data.game_exe)
        self.scale_var.set(self.config_data.scale)
        self.minimum_resolution_var.set(self.config_data.minimum_output_resolution)
        self.vram_budget_var.set(self.config_data.vram_budget_mb)
        self.anti_stretch_var.set(self.config_data.anti_stretch_boost)
        self.appearance_var.set(self.config_data.preserve_appearance)
        self.sensitive_var.set(self.config_data.protect_sensitive_textures)
        self.defer_var.set(self.config_data.defer_new_replacements)
        self.safe_var.set(self.config_data.safe_mode)
        self.alpha_var.set(self.config_data.skip_alpha_heavy)
        self._profile_changed()

    def _profile_changed(self, *_: object) -> None:
        if "Postal 2" in self.profile_var.get():
            text = (
                "DirectX 8 nativo para Postal 2. HOME alterna imediatamente entre original e "
                "aprimorada; apenas o estagio principal recebe replacement, preservando lightmaps e iluminacao."
            )
            self.toggle_button.configure(state="normal")
        else:
            text = (
                "DirectX 9/11 usa Texture Toolkit. HOME abre o painel dentro do jogo; alterne "
                "Injection para voltar aos originais sem fechar nem recarregar o jogo."
            )
            self.toggle_button.configure(state="disabled", text="Controle pelo HOME no jogo")
        self.profile_help_var.set(text)
        self._refresh_runtime_state()

    def _save_from_ui(self, notify: bool = True) -> bool:
        exe = self.exe_var.get().strip().strip('"')
        if exe and not Path(exe).is_file():
            messagebox.showerror("TextureFlow", "O executavel selecionado nao existe.")
            return False
        self.config_data.game_exe = exe
        self.config_data.profile = self.profile_var.get()
        self.config_data.scale = int(self.scale_var.get())
        self.config_data.minimum_output_resolution = int(self.minimum_resolution_var.get())
        self.config_data.vram_budget_mb = int(self.vram_budget_var.get())
        self.config_data.anti_stretch_boost = bool(self.anti_stretch_var.get())
        self.config_data.preserve_appearance = bool(self.appearance_var.get())
        self.config_data.protect_sensitive_textures = bool(self.sensitive_var.get())
        self.config_data.defer_new_replacements = bool(self.defer_var.get())
        self.config_data.safe_mode = bool(self.safe_var.get())
        self.config_data.skip_alpha_heavy = bool(self.alpha_var.get())
        save_config(CONFIG_PATH, self.config_data)
        if notify:
            messagebox.showinfo("TextureFlow", "Configuracao salva.")
        return True

    def _browse_exe(self) -> None:
        path = filedialog.askopenfilename(title="Selecione o executavel do jogo", filetypes=[("Executavel do Windows", "*.exe"), ("Todos", "*.*")])
        if path:
            self.exe_var.set(path)
            lowered = Path(path).name.lower()
            if "postal2" in lowered or "paradiselost" in lowered:
                self.profile_var.set(PROFILES[1])
            elif lowered in {"bio4.exe", "game.exe"}:
                self.profile_var.set(PROFILES[0])

    def _install(self) -> None:
        if not self._save_from_ui(False):
            return
        try:
            actions = install_texture_hook(
                ROOT, Path(self.config_data.game_exe), self.config_data.profile,
                vram_budget_mb=self.config_data.vram_budget_mb,
            )
        except Exception as exc:
            messagebox.showerror("Falha ao instalar", str(exc))
            return
        messagebox.showinfo("Captura configurada", "\n".join(actions))
        for action in actions:
            self._log(action)

    def _toggle_injection(self) -> None:
        if not self._save_from_ui(False) or not self.config_data.game_exe:
            return
        if "Postal 2" not in self.config_data.profile:
            messagebox.showinfo("TextureFlow", "Dentro do jogo, pressione HOME e alterne a opcao Injection.")
            return
        game_exe = Path(self.config_data.game_exe)
        try:
            enabled = not runtime_injection_enabled(game_exe)
            message = set_runtime_injection(game_exe, enabled)
        except OSError as exc:
            messagebox.showerror("TextureFlow", f"Nao foi possivel alternar as texturas: {exc}")
            return
        self._log(f"[RUNTIME] {message}")
        self._refresh_runtime_state()

    def _refresh_runtime_state(self) -> None:
        if not hasattr(self, "injection_var"):
            return
        if "Postal 2" not in self.profile_var.get():
            self.injection_var.set("Controle imediato: HOME > Injection")
            return
        exe = self.exe_var.get().strip().strip('"')
        enabled = not exe or runtime_injection_enabled(Path(exe))
        self.injection_var.set(
            "Aprimoradas ATIVADAS" if enabled else "Aprimoradas DESATIVADAS (originais em uso)")
        self.toggle_button.configure(
            state="normal",
            text="Desativar aprimoradas (HOME)" if enabled else "Ativar aprimoradas (HOME)",
        )

    def _promote_pending_if_safe(self, pipeline: TexturePipeline) -> None:
        count = pipeline.pending_count()
        if not self.config_data.defer_new_replacements or count == 0:
            return
        if is_game_running(Path(self.config_data.game_exe)):
            self._thread_log(
                f"[SEM POP-IN] {count} textura(s) pronta(s) ficaram em TT\\pending; "
                "serao ativadas quando o jogo fechar."
            )
            return
        activated = pipeline.activate_pending_replacements()
        if activated:
            self._thread_log(
                f"[SEM POP-IN] {activated} textura(s) ativada(s) antes da proxima abertura do jogo."
            )

    def _diagnose(self) -> None:
        if not self._save_from_ui(False) or not self.config_data.game_exe:
            return
        healthy, findings = inspect_texture_hook(Path(self.config_data.game_exe))
        title = "Instalacao pronta" if healthy else "Instalacao incompleta"
        show = messagebox.showinfo if healthy else messagebox.showwarning
        show(title, "\n".join(findings))
        for line in findings:
            self._log(f"[DIAG] {line}")

    def _open_tt(self) -> None:
        if not self._save_from_ui(False) or not self.config_data.game_exe:
            return
        folder = self.config_data.game_dir / "TT"
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            self._log(str(folder))

    def _remove(self) -> None:
        if not self._save_from_ui(False) or not self.config_data.game_exe:
            return
        if not messagebox.askyesno("Remover hook", "Feche o jogo antes de continuar. Remover somente os arquivos instalados pelo TextureFlow?"):
            return
        try:
            actions = remove_texture_hook(Path(self.config_data.game_exe))
        except Exception as exc:
            messagebox.showerror("Falha ao remover", str(exc))
            return
        messagebox.showinfo("Hook removido", "\n".join(actions))
        for action in actions:
            self._log(action)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_from_ui(False):
            return
        pipeline = TexturePipeline(ROOT, self.config_data, self._thread_log)
        missing = pipeline.validate()
        if missing:
            messagebox.showerror("TextureFlow", "Arquivos/configuracoes ausentes:\n" + "\n".join(missing))
            return
        self._promote_pending_if_safe(pipeline)
        # Re-evaluate dumps after a scale or safety-policy change; the cache signature
        # decides whether the actual neural work needs to be repeated.
        self.seen_session.clear()
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Monitorando TT\\dump")
        self.worker = threading.Thread(target=self._watch_loop, args=(pipeline,), daemon=True)
        self.worker.start()

    def _scan_once(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("TextureFlow", "O monitoramento ao vivo ja esta ativo.")
            return
        if not self._save_from_ui(False):
            return
        pipeline = TexturePipeline(ROOT, self.config_data, self._thread_log)
        missing = pipeline.validate()
        if missing:
            messagebox.showerror("TextureFlow", "Arquivos/configuracoes ausentes:\n" + "\n".join(missing))
            return
        self._promote_pending_if_safe(pipeline)
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Processando varredura")
        self.worker = threading.Thread(target=self._one_scan, args=(pipeline,), daemon=True)
        self.worker.start()

    def _one_scan(self, pipeline: TexturePipeline) -> None:
        files = list(pipeline.scan())
        self._thread_log(f"Varredura encontrou {len(files)} DDS.")
        for path in files:
            if self.stop_event.is_set():
                break
            if wait_until_stable(path, self.config_data.stable_seconds, self.stop_event):
                self._handle_result(pipeline.process_one(path))
        self._promote_pending_if_safe(pipeline)
        self.messages.put(("stopped", None))

    def _watch_loop(self, pipeline: TexturePipeline) -> None:
        self._thread_log(f"Monitorando: {self.config_data.dump_dir}")
        if self.config_data.defer_new_replacements:
            self._thread_log(
                "Modo sem pop-in ativo: novas texturas vao para TT\\pending e so entram depois que o jogo fechar."
            )
        else:
            self._thread_log("Modo imediato ativo: novas texturas entram em TT\\inject assim que ficam prontas.")
        hook_log = self.config_data.game_dir / "TextureFlowD3D8.log"
        hook_log_position = hook_log.stat().st_size if hook_log.is_file() else 0
        last_promotion_check = 0.0
        while not self.stop_event.is_set():
            try:
                if pipeline.invalidate_cache_if_removed():
                    self.seen_session.clear()
                    self._thread_log(
                        "[CACHE] cache.json foi removido; todos os dumps serao avaliados novamente."
                    )
                for path in pipeline.scan():
                    stat = path.stat()
                    marker = (stat.st_size, stat.st_mtime_ns)
                    key = str(path).lower()
                    observed = self.seen_session.get(key)
                    if observed and observed[0] == marker:
                        previous_status = observed[1]
                        if previous_status not in {"processed", "cached"} or pipeline.has_output(path.name):
                            continue
                        self._thread_log(
                            f"[CACHE] {path.name}: replacement apagado; regenerando automaticamente."
                        )
                    self.seen_session[key] = (marker, "processing")
                    if wait_until_stable(path, self.config_data.stable_seconds, self.stop_event):
                        result = pipeline.process_one(path)
                        self.seen_session[key] = (marker, result.status)
                        self._handle_result(result)
                if "Postal 2" in self.config_data.profile and hook_log.is_file():
                    size = hook_log.stat().st_size
                    if size < hook_log_position:
                        hook_log_position = 0
                    if size > hook_log_position:
                        with hook_log.open("r", encoding="utf-8", errors="replace") as stream:
                            stream.seek(hook_log_position)
                            for line in stream:
                                self._thread_log(f"[D3D8] {line.rstrip()}")
                            hook_log_position = stream.tell()
                if self.config_data.defer_new_replacements and time.monotonic() - last_promotion_check >= 2.0:
                    last_promotion_check = time.monotonic()
                    self._promote_pending_if_safe(pipeline)
            except Exception as exc:
                self.messages.put(("log", f"[ERRO] varredura: {exc}"))
            self.stop_event.wait(self.config_data.poll_seconds)
        self.messages.put(("stopped", None))

    def _handle_result(self, result: object) -> None:
        self.messages.put(("result", result))

    def _thread_log(self, message: str) -> None:
        self.messages.put(("log", message))

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Parando...")

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "result":
                    result = payload
                    status = getattr(result, "status", "error")
                    if status == "processed":
                        self.processed += 1; prefix = "OK"
                    elif status in {"skipped", "cached"}:
                        self.skipped += 1; prefix = "CACHE" if status == "cached" else "PULA"
                    else:
                        self.errors += 1; prefix = "ERRO"
                    name = Path(getattr(result, "source", "?")).name
                    reason = getattr(result, "reason", "")
                    elapsed = getattr(result, "elapsed_seconds", 0.0)
                    self._log(f"[{prefix}] {name}: {reason} ({elapsed:.1f}s)")
                    self.stats_var.set(f"Processadas: {self.processed}  |  Ignoradas/cache: {self.skipped}  |  Erros: {self.errors}")
                elif kind == "stopped":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("Parado")
        except queue.Empty:
            pass
        if time.monotonic() - self._last_runtime_poll >= 0.5:
            self._last_runtime_poll = time.monotonic()
            self._refresh_runtime_state()
        self.after(100, self._drain_messages)

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> None:
    try:
        TextureFlowApp().mainloop()
    except TextureFlowError as exc:
        messagebox.showerror("TextureFlow", str(exc))


if __name__ == "__main__":
    main()
