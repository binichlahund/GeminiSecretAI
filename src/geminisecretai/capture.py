"""Screen/file selection, separate from the control-panel layout."""
import os
import time
import tkinter as tk
from tkinter import filedialog
from .display import selection_bounds
from .paths import PATHS

class CaptureActions:

    def choose_image(self):
        if self.app.shutdown.is_set() or self.closing:
            return
        if self.app.state != self.app.IDLE:
            self.set_message('Finish or cancel the current question first.')
            return
        path = filedialog.askopenfilename(parent=self.root, title='Choose an image for Gemini', filetypes=[('Image files', '*.png *.jpg *.jpeg *.webp *.gif *.bmp'), ('All files', '*.*')])
        if path:
            self.app.send_image(path)

    def capture_area(self):
        if self.app.shutdown.is_set() or self.closing:
            return
        if self.app.state not in (self.app.IDLE, self.app.CAPTURING):
            self.set_message('Wait for the current request to finish before capturing an image.')
            return
        if getattr(self, 'capture_overlay', None) is not None:
            return
        self.app.state = self.app.CAPTURING
        self.set_message('Drag over the part of the screen you want Gemini to answer.')
        desktop = self.app.display.desktop_bounds()
        overlay = tk.Toplevel(self.root)
        self.capture_overlay = overlay
        overlay.withdraw()
        overlay.overrideredirect(True)
        left, top, right, bottom = desktop
        overlay.geometry(f'{right - left}x{bottom - top}+0+0')
        overlay.attributes('-topmost', True)
        overlay.attributes('-alpha', 0.22)
        overlay.configure(bg='black')
        canvas = tk.Canvas(overlay, bg='black', cursor='crosshair', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        selection = {'start': None, 'rect': None}

        def begin(event):
            selection['start'] = self.app.display.cursor_position()
            selection['rect'] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline='#dddddf', width=3)

        def move(event):
            if selection['start']:
                x, y = selection['start']
                end_x, end_y = self.app.display.cursor_position()
                canvas.coords(selection['rect'], x - left, y - top, end_x - left, end_y - top)

        def close_overlay(reserve=False):
            self.capture_overlay = None
            overlay.destroy()
            self.app.state = self.app.CAPTURING if reserve else self.app.IDLE

        def finish(event):
            if not selection['start']:
                return
            x1, y1, x2, y2 = selection_bounds(selection['start'], self.app.display.cursor_position(), desktop)
            close_overlay(reserve=True)
            if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
                self.app.state = self.app.IDLE
                self.set_message('Capture cancelled — choose a larger area.')
                return
            self.capture_after_id = self.root.after(150, lambda: self.save_capture(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        overlay.bind('<Escape>', lambda event: (close_overlay(), self.set_message('Capture cancelled.')))
        canvas.bind('<ButtonPress-1>', begin)
        canvas.bind('<B1-Motion>', move)
        canvas.bind('<ButtonRelease-1>', finish)
        overlay.deiconify()
        self.app.display.place(overlay, desktop)
        overlay.focus_force()

    def save_capture(self, x1, y1, x2, y2):
        self.capture_after_id = None
        if self.app.shutdown.is_set() or self.closing:
            self.app.state = self.app.IDLE
            return
        try:
            folder = str(PATHS.captures)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f'capture-{int(time.time() * 1000)}.png')
            self.app.ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True).save(path)
            self.app.send_image(path, delete_after=True)
        except Exception as exc:
            self.app.state = self.app.IDLE
            self.app.log.exception('Screen capture failed')
            self.set_message(f'Error: Could not capture the screen: {exc}', error=True)
