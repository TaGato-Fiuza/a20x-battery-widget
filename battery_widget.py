"""Widget na barra de tarefas com a bateria do Astro A20 X e do mouse G305.

Fica logo a esquerda da bandeja (relogio/icones), sempre por cima da barra.
Mostra so os icones; passar o mouse por cima mostra as porcentagens.
Clique fixa aberto; outro clique desfixa.
Clique direito: atualizar / sair. Some sozinho quando um app esta em tela cheia.
"""
import ctypes
import threading
import time
import tkinter as tk
import winreg
from ctypes import wintypes

from devices import A20X, LogitechMouse

POLL_SECONDS = 60
LOW_BATTERY = 15
HOVER_OPEN_SECONDS = 0.4  # mouse parado em cima por esse tempo abre as porcentagens
HOVER_CLOSE_SECONDS = 0.5  # fecha esse tempo depois que o mouse sai (se nao estiver fixado)
ANIMATION_SECONDS = 0.22  # duracao da animacao de abrir/fechar
GLYPH_HEADSET = "\ue7f6"  # Segoe Fluent Icons
GLYPH_MOUSE = "\ue962"  # Segoe Fluent Icons
GLYPH_CHARGING = "\ue945"  # Segoe Fluent Icons

user32 = ctypes.windll.user32


def light_theme():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def fullscreen_app_active():
    fg = user32.GetForegroundWindow()
    if not fg or class_name(fg) in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    l, t, r, b = window_rect(fg)
    return l <= 0 and t <= 0 and r >= user32.GetSystemMetrics(0) and b >= user32.GetSystemMetrics(1)


def hex_to_rgb(c):
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def blend(c1, c2, t):
    """Mistura duas cores #rrggbb (t=0 -> c1, t=1 -> c2)."""
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def ease_in_out(t):
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = ctypes.c_void_p(-1)  # precisa ser ponteiro de 64 bits, nao int de 32
user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10


class Widget:
    def __init__(self):
        self.devices = [("headset", GLYPH_HEADSET, A20X()), ("mouse", GLYPH_MOUSE, LogitechMouse())]
        self.state = {name: None for name, _, _ in self.devices}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()

        light = light_theme()
        self.fg = "#1a1a1a" if light else "#ffffff"
        self.dim = "#8a8a8a"
        self.low = "#e81123" if light else "#ff6b6b"
        self.charge = "#107c10" if light else "#6ccb5f"
        # Fundo opaco com a cor da barra (lida da tela), para o widget inteiro ser clicavel.
        # Uma cor transparente deixaria so o traco fino do icone receber o clique.
        self.key = "#eeeeee" if light else "#1c1c1c"

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=self.key)
        self.frame = tk.Frame(self.root, bg=self.key)
        self.frame.place(relx=1.0, x=-8, rely=0.5, anchor="e")  # alinhado a direita

        self.icon_font = ("Segoe Fluent Icons", 12)
        self.small_icon_font = ("Segoe Fluent Icons", 8)
        self.text_font = ("Segoe UI Variable Text", 11)

        # Cada aparelho: icone + "cortina" (frame de largura animada) com texto e raio dentro.
        self.widgets = {}
        for name, glyph, _ in self.devices:
            icon = tk.Label(self.frame, text=glyph, font=self.icon_font, bg=self.key, fg=self.fg)
            reveal = tk.Frame(self.frame, bg=self.key, height=1, width=1)
            reveal.pack_propagate(False)
            text = tk.Label(reveal, text="--", font=self.text_font, bg=self.key, fg=self.fg)
            bolt = tk.Label(reveal, text=GLYPH_CHARGING, font=self.small_icon_font, bg=self.key, fg=self.charge)
            # place() (em vez de pack) nao encolhe o texto: a cortina estreita so corta a direita.
            text.place(x=3, rely=0.5, anchor="w")
            self.widgets[name] = {"icon": icon, "reveal": reveal, "text": text, "bolt": bolt,
                                  "shown": False, "low": False, "charging": False,
                                  "full_w": 0, "full_h": 1, "text_fg": self.fg}

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Atualizar agora", command=self.wake.set)
        self.menu.add_separator()
        self.menu.add_command(label="Sair", command=self.quit)

        self.expanded = False
        self.pinned = False
        self.hover_blocked = False
        self.entered_at = None
        self.left_at = None
        self.progress = 0.0  # 0 = fechado, 1 = aberto (animado)
        self.anim_job = None
        self.anim_last = None
        self.hidden = False
        # A barra de tarefas as vezes fica por cima do widget e recebe o clique no lugar dele,
        # entao o clique esquerdo e detectado olhando o botao e a posicao do mouse (watch_mouse).
        # O menu do clique direito so funciona quando o widget esta por cima (o normal).
        self.root.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self.mouse_was_down = False

        self.root.update_idletasks()
        self.hwnd = int(self.root.wm_frame(), 16)
        # Daqui em diante nao usar root.attributes(): o Tk reescreve o estilo estendido
        # e apaga o WS_EX_TOOLWINDOW, fazendo aparecer um botao do Python na barra.
        self.ensure_style()

        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.render()
        self.tick()
        self.watch_mouse()

    # ---------- janela ----------

    def ensure_style(self):
        ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        want = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW & 0xFFFFFFFF
        if ex != want:
            signed = want - (1 << 32) if want >= (1 << 31) else want
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, signed)
            if not self.hidden:  # a barra so rele o estilo quando a janela reaparece
                user32.ShowWindow(self.hwnd, SW_HIDE)
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    def set_hidden(self, hidden):
        if hidden != self.hidden:
            self.hidden = hidden
            user32.ShowWindow(self.hwnd, SW_HIDE if hidden else SW_SHOWNOACTIVATE)

    # ---------- abrir / fechar ----------

    def set_expanded(self, expanded):
        if expanded == self.expanded:
            return
        self.expanded = expanded
        if expanded:
            self.wake.set()  # atualiza ao abrir
        if self.anim_job is None:
            self.anim_last = time.monotonic()
            self.animate()

    def animate(self):
        now = time.monotonic()
        step = (now - self.anim_last) / ANIMATION_SECONDS
        self.anim_last = now
        target = 1.0 if self.expanded else 0.0
        if self.progress < target:
            self.progress = min(target, self.progress + step)
        else:
            self.progress = max(target, self.progress - step)
        self.layout()
        if self.progress != target:
            self.anim_job = self.root.after(12, self.animate)
        else:
            self.anim_job = None

    def on_click(self):
        if self.pinned:
            # Desfixa e fecha; so reabre por hover depois que o mouse sair e voltar.
            self.pinned = False
            self.hover_blocked = True
            self.set_expanded(False)
        else:
            self.pinned = True
            self.set_expanded(True)

    def quit(self):
        self.stop.set()
        self.wake.set()
        self.root.destroy()

    # ---------- dados ----------

    def poll_loop(self):
        while not self.stop.is_set():
            for name, _, dev in self.devices:
                try:
                    value = dev.read_battery()
                except Exception:
                    value = None
                with self.lock:
                    self.state[name] = value
            self.root.after(0, self.render)
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def render(self):
        """Atualiza textos e cores a partir do estado; o tamanho fica com layout()."""
        with self.lock:
            state = dict(self.state)
        for name, _, _ in self.devices:
            w = self.widgets[name]
            value = state[name]
            w["shown"] = value is not None or name == "headset"  # mouse desligado: some
            if value is None:
                w["low"], w["charging"] = False, False
                w["icon"].configure(fg=self.dim)
                w["text"].configure(text="--")
                w["text_fg"] = self.dim
            else:
                level, charging = value
                w["low"] = level <= LOW_BATTERY and not charging
                w["charging"] = charging
                color = self.low if w["low"] else self.fg
                w["icon"].configure(fg=color)
                w["text"].configure(text=f"{level}%")
                w["text_fg"] = color
            if w["charging"]:
                w["bolt"].place(x=3 + w["text"].winfo_reqwidth() + 1, rely=0.5, anchor="w")
            else:
                w["bolt"].place_forget()
            w["full_w"] = w["text"].winfo_reqwidth() + 3
            if w["charging"]:
                w["full_w"] += w["bolt"].winfo_reqwidth() + 1
            w["full_h"] = max(w["text"].winfo_reqheight(), w["icon"].winfo_reqheight())
        # Monta a ordem uma vez aqui; a animacao so muda larguras (nada e remontado por quadro).
        for child in self.frame.pack_slaves():
            child.pack_forget()
        for name, _, _ in self.devices:
            w = self.widgets[name]
            if w["shown"]:
                w["icon"].pack(side="left")
                w["reveal"].pack(side="left")
        self.layout()
        self.place()

    def layout(self):
        e = ease_in_out(self.progress)
        first = True
        for name, _, _ in self.devices:
            w = self.widgets[name]
            if not w["shown"]:
                continue
            reveal = 1.0 if w["low"] else e  # bateria baixa sempre mostra o numero
            gap = 0 if first else round(6 + 4 * e)
            first = False
            w["icon"].pack_configure(padx=(gap, 0))
            w["reveal"].configure(width=max(1, round(w["full_w"] * reveal)), height=w["full_h"])
            w["text"].configure(fg=blend(self.key, w["text_fg"], reveal))
            w["bolt"].configure(fg=blend(self.key, self.charge, reveal))

    def max_width(self):
        """Largura do conteudo totalmente aberto: a janela fica sempre com esse tamanho."""
        total, first = 0, True
        for name, _, _ in self.devices:
            w = self.widgets[name]
            if w["shown"]:
                total += (0 if first else 10) + w["icon"].winfo_reqwidth() + w["full_w"]
                first = False
        return total

    def place(self):
        """Janela de largura fixa (a do modo aberto) com o conteudo alinhado a direita,
        para a animacao nao precisar mover nem redimensionar a janela."""
        tb = user32.FindWindowW("Shell_TrayWnd", None)
        if not tb:
            return
        tl, tt, tr, tbm = window_rect(tb)
        tray = user32.FindWindowExW(tb, None, "TrayNotifyWnd", None)
        right = window_rect(tray)[0] if tray else tr - 300
        w = self.max_width() + 16
        h = tbm - tt
        geometry = f"{w}x{h}+{right - w - 4}+{tt}"
        if geometry != self.root.geometry():
            self.root.geometry(geometry)

    def content_rect(self):
        """Retangulo na tela so do conteudo visivel (para hover e clique)."""
        _, t, r, b = window_rect(self.hwnd)
        left = self.frame.winfo_rootx()
        return left, t, r, b

    # ---------- mouse ----------

    def watch_mouse(self):
        if self.stop.is_set():
            return
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        l, t, r, b = self.content_rect()
        inside = not self.hidden and l - 4 <= pt.x < r and t <= pt.y < b
        now = time.monotonic()

        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON
        if down and not self.mouse_was_down and inside:
            self.on_click()
        self.mouse_was_down = down

        if inside:
            self.left_at = None
            if self.entered_at is None:
                self.entered_at = now
            if not self.hover_blocked and not self.expanded and now - self.entered_at >= HOVER_OPEN_SECONDS:
                self.set_expanded(True)
        else:
            self.entered_at = None
            self.hover_blocked = False
            if self.left_at is None:
                self.left_at = now
            if self.expanded and not self.pinned and now - self.left_at >= HOVER_CLOSE_SECONDS:
                self.set_expanded(False)
        self.root.after(30, self.watch_mouse)

    # ---------- manutencao (1x por segundo) ----------

    def sync_background(self):
        """Copia a cor da barra logo a esquerda do widget (a barra muda com o papel de parede)."""
        x, y = self.root.winfo_x() - 6, self.root.winfo_y() + 4
        hdc = user32.GetDC(0)
        color = ctypes.windll.gdi32.GetPixel(hdc, x, y)
        user32.ReleaseDC(0, hdc)
        if color == 0xFFFFFFFF:  # CLR_INVALID
            return
        bg = f"#{color & 0xFF:02x}{(color >> 8) & 0xFF:02x}{(color >> 16) & 0xFF:02x}"
        if bg == self.key:
            return
        self.key = bg
        parts = [self.root, self.frame]
        for d in self.widgets.values():
            parts += [d["icon"], d["reveal"], d["text"], d["bolt"]]
        for w in parts:
            w.configure(bg=bg)
        self.layout()

    def tick(self):
        if self.stop.is_set():
            return
        if fullscreen_app_active():
            self.set_hidden(True)
        else:
            self.set_hidden(False)
            if self.anim_job is None:
                self.place()
                self.sync_background()
            self.ensure_style()
        self.root.after(1000, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "A20XBatteryWidget")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ja esta rodando
        raise SystemExit
    Widget().run()
