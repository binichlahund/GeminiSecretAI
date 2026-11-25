"""Matte Windows control panel. Runtime actions are supplied by the client."""
import tkinter as tk
import customtkinter as ctk


class ControlPanel:
    BG, SURFACE, FIELD, BORDER = "#111112", "#1a1a1c", "#202023", "#343438"
    TEXT, MUTED, ACCENT = "#eeeeef", "#a3a3aa", "#dddddf"
    GREEN, ORANGE, RED = "#91b6a2", "#c9b582", "#d89a98"

    def __init__(self, root, app):
        self.root, self.app = root, app
        self.hidden, self.closing = False, False
        self.capture_after_id = None
        root.title("GeminiSecretAI")
        # Fit the actual work area, accounting for CTk's logical dimensions.
        left, top, right, bottom = app.display.active_work_area()
        scale = max(1, root._get_window_scaling())
        width = min(760, int((right - left) / scale) - 40)
        height = min(760, int((bottom - top) / scale) - 50)
        root.geometry(f"{max(360, width)}x{max(300, height)}")
        root.minsize(360, 300)
        root.configure(fg_color=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        shell = ctk.CTkFrame(root, fg_color=self.BG, corner_radius=0)
        shell.pack(fill="both", expand=True, padx=24, pady=14)
        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        self.label(header, "GeminiSecretAI", size=18, bold=True).pack(side="left")
        self.secondary_button(header, "Quit", self.quit, width=64).pack(side="right")
        self.navigation = ctk.CTkSegmentedButton(
            shell, values=["Home", "Settings", "Help"], command=self.show_page,
            height=36, corner_radius=5, fg_color=self.SURFACE,
            selected_color="#36363a", selected_hover_color="#414146",
            unselected_color=self.SURFACE, unselected_hover_color="#262629",
            text_color=self.TEXT, font=ctk.CTkFont(size=13),
        )
        self.navigation.pack(fill="x", pady=(0, 10))
        self.message = ctk.CTkLabel(
            shell, text="", anchor="w", justify="left",
            text_color=self.MUTED, height=24, font=ctk.CTkFont(size=12), wraplength=620,
        )
        self.message.pack(fill="x", pady=(0, 8))
        self.body = ctk.CTkFrame(shell, fg_color="transparent", corner_radius=0)
        self.body.pack(fill="both", expand=True)
        self.pages = {}
        for name in ("Home", "Settings", "Help"):
            page = ctk.CTkScrollableFrame(
                self.body, fg_color=self.BG, corner_radius=0,
                scrollbar_button_color="#38383d", scrollbar_button_hover_color="#55555c")
            self.pages[name] = page
        self.build_home()
        self.build_settings()
        self.build_help()
        self.configure_keyboard_access(shell)
        self.show_page("Home")
        root.bind("<Configure>", self.on_resize, add="+")
        self.bridge_status = {"phase": "starting"}
        import threading
        threading.Thread(target=self.poll_bridge, name="status-poll", daemon=True).start()
        self.refresh_status()
        root.after(150, self.pump)

    def label(self, parent, text="", size=13, bold=False, muted=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=self.MUTED if muted else self.TEXT,
                           font=ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal"),
                           anchor="w", justify="left", **kwargs)

    def separator(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=self.BORDER, corner_radius=0).pack(fill="x", pady=12)

    def heading(self, parent, title, subtitle=None):
        self.label(parent, title, size=20, bold=True).pack(anchor="w", pady=(2, 4))
        if subtitle:
            self.label(parent, subtitle, muted=True, wraplength=620, height=22).pack(fill="x", pady=(0, 10))

    def primary_button(self, parent, text, command, width=140):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=36,
                             corner_radius=5, fg_color=self.ACCENT, hover_color="#f0f0f1",
                             text_color="#171719", font=ctk.CTkFont(size=13, weight="bold"))

    def secondary_button(self, parent, text, command, width=140):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=36,
                             corner_radius=5, fg_color=self.FIELD, hover_color="#303034",
                             border_width=1, border_color=self.BORDER, text_color=self.TEXT,
                             font=ctk.CTkFont(size=13))

    def build_home(self):
        page = self.pages["Home"]
        self.heading(page, "Ask Gemini", "Use a shortcut for text, or choose an image below.")
        status = ctk.CTkFrame(page, fg_color=self.SURFACE, corner_radius=6)
        status.pack(fill="x")
        self.label(status, "CONNECTION", size=11, muted=True, height=18).pack(anchor="w", padx=16, pady=(12, 2))
        self.status_title = self.label(status, "Starting", size=16, bold=True)
        self.status_title.pack(anchor="w", padx=16)
        self.status_detail = self.label(status, "Checking Gemini sign-in.", muted=True, wraplength=580, height=22)
        self.status_detail.pack(fill="x", padx=16, pady=(2, 12))
        self.request_status = self.label(page, "No question in progress.", muted=True, wraplength=600)
        self.request_status.pack(fill="x", pady=(8, 8))
        self.action_row = ctk.CTkFrame(page, fg_color="transparent")
        self.action_row.pack(fill="x")
        self.capture_button = self.primary_button(self.action_row, "Capture screenshot", self.capture_area)
        self.upload_button = self.secondary_button(self.action_row, "Upload image", self.choose_image)
        self.connection_row = ctk.CTkFrame(page, fg_color="transparent")
        self.connection_row.pack(fill="x", pady=(10, 0))
        self.login_button = self.secondary_button(self.connection_row, "Open login", lambda: self.mode(False))
        self.run_button = self.secondary_button(self.connection_row, "Run headlessly", lambda: self.mode(True))
        self.cancel_button = self.secondary_button(self.connection_row, "Cancel question", self.cancel_current)
        self.separator(page)
        self.background_row = ctk.CTkFrame(page, fg_color="transparent")
        self.background_row.pack(fill="x")
        self.exit_hint = self.label(self.background_row, self.exit_hint_text(), size=12, muted=True, wraplength=450)
        self.exit_hint.pack(side="left", fill="x", expand=True, padx=(0, 14))
        self.hide_button = self.secondary_button(self.background_row, "Hide app", self.hide_app, width=110)
        self.hide_button.pack(side="right")
        self.separator(page)
        self.label(page, "Shortcuts", size=14, bold=True).pack(anchor="w", pady=(0, 6))
        self.shortcut_labels = {}
        for key, title in (("hotkey", "Text question"), ("image_hotkey", "Screen question"), ("exit_hotkey", "Exit app")):
            row = ctk.CTkFrame(page, fg_color="transparent")
            row.pack(fill="x", pady=3)
            self.label(row, title, muted=True, height=22).pack(side="left")
            label = self.label(row, self.app.cfg[key], size=13, height=22)
            label.configure(font=ctk.CTkFont(family="Consolas", size=13))
            label.pack(side="right")
            self.shortcut_labels[key] = label

    def build_settings(self):
        page = self.pages["Settings"]
        self.heading(page, "Settings", "Changes stay on this PC.")
        self.settings_navigation = ctk.CTkSegmentedButton(
            page, values=["Shortcuts", "Instructions", "Advanced"], command=self.show_settings_section,
            height=34, corner_radius=5, fg_color=self.SURFACE, selected_color="#36363a",
            selected_hover_color="#414146", unselected_color=self.SURFACE,
            unselected_hover_color="#262629", text_color=self.TEXT, font=ctk.CTkFont(size=12))
        self.settings_navigation.pack(fill="x", pady=(0, 16))
        self.settings_sections = {name: ctk.CTkFrame(page, fg_color="transparent") for name in
                                  ("Shortcuts", "Instructions", "Advanced")}
        page = self.settings_sections["Shortcuts"]
        self.label(page, "Keyboard shortcuts", size=15, bold=True).pack(anchor="w", pady=(0, 10))
        for attr, key, title in (("hotkey", "hotkey", "Text question"), ("image_hotkey", "image_hotkey", "Screen question"), ("exit_hotkey", "exit_hotkey", "Exit app")):
            self.label(page, title, muted=True).pack(anchor="w", pady=(4, 3))
            field = ctk.CTkEntry(page, height=36, corner_radius=5, fg_color=self.FIELD,
                                 border_color=self.BORDER, text_color=self.TEXT,
                                 font=ctk.CTkFont(family="Consolas", size=13))
            field.insert(0, self.app.cfg[key])
            field.pack(fill="x", pady=(0, 6))
            field.bind("<Return>", lambda event: self.save_keybinds())
            field.bind("<KeyRelease>", lambda event: self.mark_dirty("shortcuts"), add="+")
            setattr(self, attr, field)
        self.hotkey_note = self.label(page, "Use different shortcuts. Ctrl+Alt+Del is reserved by Windows.", size=12, muted=True, wraplength=600, height=36)
        self.hotkey_note.pack(fill="x")
        self.secondary_button(page, "Save shortcuts", self.save_keybinds).pack(anchor="w", pady=(0, 6))
        self.separator(page)
        self.dot_enabled = tk.BooleanVar(value=self.app.cfg["dot"])
        ctk.CTkSwitch(page, text="Show status dot", variable=self.dot_enabled, command=self.toggle_dot,
                      progress_color="#79797f", button_color="#dedee1", button_hover_color="#f5f5f5",
                      font=ctk.CTkFont(size=13)).pack(anchor="w")
        self.label(page, "Applies immediately, including while the app is hidden.", size=12, muted=True, wraplength=600).pack(fill="x", pady=(5, 0))
        page = self.settings_sections["Instructions"]
        self.label(page, "Answer instructions", size=15, bold=True).pack(anchor="w", pady=(0, 12))
        self.prompt_enabled = tk.BooleanVar(value=self.app.cfg["system_prompt_enabled"])
        ctk.CTkSwitch(page, text="Use saved instructions", variable=self.prompt_enabled, command=self.save_system_prompt,
                      progress_color="#79797f", button_color="#dedee1", font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(0, 12))
        self.system_prompt = ctk.CTkTextbox(page, height=170, corner_radius=5, fg_color=self.FIELD,
                                          border_width=1, border_color=self.BORDER,
                                          text_color=self.TEXT, font=ctk.CTkFont(size=13), wrap="word")
        self.system_prompt.insert("1.0", self.app.cfg["system_prompt"])
        self.system_prompt.pack(fill="x")
        self.system_prompt.bind("<KeyRelease>", lambda event: self.mark_dirty("prompt"), add="+")
        self.system_note = self.label(page, "Included with text and image questions.", size=12, muted=True, wraplength=600, height=36)
        self.system_note.pack(fill="x", pady=(4, 4))
        row = ctk.CTkFrame(page, fg_color="transparent")
        row.pack(fill="x")
        self.primary_button(row, "Save instructions", self.save_system_prompt).pack(side="left", padx=(0, 8))
        self.secondary_button(row, "Restore default", self.restore_system_prompt).pack(side="left")
        self.advanced = self.settings_sections["Advanced"]
        self.label(self.advanced, "Advanced settings", size=15, bold=True).pack(anchor="w")
        self.label(self.advanced, "Connection changes apply after restarting the app.", muted=True, wraplength=600).pack(fill="x", pady=(12, 8))
        self.advanced_vars = {}
        for key, title in (("bridge_port", "Local connection port"), ("request_timeout", "Request timeout (seconds)")):
            self.label(self.advanced, title, muted=True).pack(anchor="w", pady=(6, 3))
            field = ctk.CTkEntry(self.advanced, height=36, fg_color=self.FIELD,
                                 border_color=self.BORDER, text_color=self.TEXT, corner_radius=5)
            field.insert(0, str(self.app.cfg[key]))
            field.pack(fill="x")
            field.bind("<KeyRelease>", lambda event: self.mark_dirty("advanced"), add="+")
            self.advanced_vars[key] = field
        self.suppress_var = tk.BooleanVar(value=self.app.cfg["suppress_keys"])
        ctk.CTkCheckBox(self.advanced, text="Keep recorded typing out of other apps", variable=self.suppress_var,
                        command=lambda: self.mark_dirty("advanced"),
                        fg_color="#79797f", hover_color="#99999f").pack(anchor="w", pady=14)
        self.advanced_note = self.label(self.advanced, "", size=12, muted=True, height=36, wraplength=600)
        self.advanced_note.pack(fill="x")
        self.secondary_button(self.advanced, "Save advanced", self.save_advanced).pack(anchor="w", pady=(4, 12))
        self.show_settings_section("Shortcuts", resize=False)

    def build_help(self):
        from .paths import PATHS
        page = self.pages["Help"]
        self.heading(page, "Help", "A quick guide to getting answers and staying in control.")
        topics = [
            ("Text questions", "Press the text shortcut, type your question, and press it again. Wait for the answer, then paste with Ctrl+V. Esc cancels recording."),
            ("Screen questions", "Press the screen shortcut and drag over the question. Release to send it. Esc cancels the selection. Use Upload image for an existing file."),
            ("First sign-in", "On Home, choose Open login. Sign in to Google in that browser, then return here and choose Run headlessly."),
            ("Hidden mode", "Hide app removes the control window and taskbar entry. Your question shortcuts still work. Use the saved exit shortcut or Task Manager, then relaunch to see settings."),
            ("Something went wrong?", "Check your connection status on Home. Cancel a stuck question before trying again. If sign-in expired, open the login window. Diagnostic logs can help identify a changed Gemini page."),
        ]
        for title, text in topics:
            self.label(page, title, size=14, bold=True).pack(anchor="w", pady=(14, 4))
            self.label(page, text, muted=True, wraplength=600).pack(fill="x")
        self.separator(page)
        for title, path in (("Open logs folder", PATHS.logs), ("Open project folder", PATHS.root), ("Read the README", PATHS.root / "README.md")):
            self.secondary_button(page, title, lambda p=path: self.open_location(p), width=190).pack(anchor="w", pady=4)
        self.label(page, "Settings and logs: data/\nExisting browser login profiles stay in their original location.", size=12, muted=True, wraplength=600).pack(fill="x", pady=(14, 8))

    def show_page(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self.navigation.set(name)
        self.active_page = name
        self.pages[name]._parent_canvas.yview_moveto(0)
        self.on_resize()

    def show_settings_section(self, name, resize=True):
        for section in self.settings_sections.values():
            section.pack_forget()
        self.settings_sections[name].pack(fill="x")
        self.settings_navigation.set(name)
        self.pages["Settings"]._parent_canvas.yview_moveto(0)
        if resize:
            self.on_resize()

    def mark_dirty(self, section):
        label = {"shortcuts": self.hotkey_note, "prompt": self.system_note,
                 "advanced": self.advanced_note}[section]
        label.configure(text="Unsaved changes", text_color=self.ORANGE)

    def configure_keyboard_access(self, parent):
        """Give CTk canvas controls normal Tab, Enter and Space behavior."""
        for widget in parent.winfo_children():
            if isinstance(widget, ctk.CTkButton):
                widget._canvas.configure(takefocus=False)
                widget._text_label.configure(takefocus=True)
                border, thickness = widget.cget("border_color"), widget.cget("border_width")
                widget.bind("<FocusIn>", lambda event, w=widget: (
                    w.configure(border_color=self.ACCENT, border_width=1), self.reveal_focus(w)))
                widget.bind("<FocusOut>", lambda event, w=widget, b=border, t=thickness:
                            w.configure(border_color=b, border_width=t))
                for key in ("<Return>", "<space>"):
                    widget.bind(key, lambda event, w=widget: w.invoke())
            elif isinstance(widget, (ctk.CTkSwitch, ctk.CTkCheckBox)):
                widget._canvas.configure(takefocus=False)
                widget._text_label.configure(takefocus=True, highlightthickness=1,
                                             highlightbackground=self.BG, highlightcolor=self.ACCENT)
                for key in ("<Return>", "<space>"):
                    widget.bind(key, lambda event, w=widget: w.toggle())
                widget.bind("<FocusIn>", lambda event, w=widget: self.reveal_focus(w))
            elif isinstance(widget, ctk.CTkEntry):
                widget.bind("<FocusIn>", lambda event, w=widget: (
                    w.configure(border_color=self.ACCENT), self.reveal_focus(w)))
                widget.bind("<FocusOut>", lambda event, w=widget: w.configure(border_color=self.BORDER))
            self.configure_keyboard_access(widget)

    def reveal_focus(self, widget):
        page = self.pages.get(getattr(self, "active_page", "Home"))
        canvas = page._parent_canvas
        self.root.update_idletasks()
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        if top < 0 or bottom > canvas.winfo_height():
            offset = top - 12 if top < 0 else bottom - canvas.winfo_height() + 12
            region = canvas.bbox("all")
            if region and region[3] > canvas.winfo_height():
                canvas.yview_moveto(max(0, canvas.yview()[0] + offset / region[3]))

    def on_resize(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        width = max(220, int(self.root.winfo_width() / max(1, self.root._get_window_scaling())) - 82)
        for label in (self.message, self.status_detail, self.request_status, self.exit_hint,
                      self.hotkey_note, self.system_note, self.advanced_note):
            label.configure(wraplength=width)
        def wrap_children(parent):
            for widget in parent.winfo_children():
                if isinstance(widget, ctk.CTkLabel) and widget.cget("wraplength"):
                    widget.configure(wraplength=width)
                wrap_children(widget)
        for page in self.pages.values():
            wrap_children(page)
        self.exit_hint.configure(wraplength=max(110, width - 135))
        narrow = width < 500
        for row, buttons in ((self.action_row, (self.capture_button, self.upload_button)),
                             (self.connection_row, (self.login_button, self.run_button, self.cancel_button))):
            for button in buttons:
                button.pack_forget()
                button.pack(side="top" if narrow else "left", fill="x",
                            expand=not narrow, padx=(0, 0 if narrow else 6), pady=(0, 6))

    def update_shortcuts(self):
        for key, label in self.shortcut_labels.items():
            label.configure(text=self.app.cfg[key])
        self.exit_hint.configure(text=self.exit_hint_text())
