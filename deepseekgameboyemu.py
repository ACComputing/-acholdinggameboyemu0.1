#!/usr/bin/env python3
# ============================================================
# DeepSeek's GameBoy Emu 0.1
# AC + DeepSeek
# (C) 1999-2026 A.C Holdings
# ============================================================

import tkinter as tk
from tkinter import filedialog
import time
import random

# ================= CONFIG =================
W, H = 160, 144
SCALE = 3

BG = "#000000"          # Pure black — DeepSeek mode
PANEL = "#0a0a0a"       # Slightly lighter black
ACCENT = "#00aaff"      # DeepSeek blue
TEXT = "#00aaff"        # Blue text everywhere

# ============================================================
# 🧠 DEEPSEEK CORE (DMG-style)
# ============================================================
class DeepSeekGB:
    def __init__(self):
        self.mem = bytearray(0x10000)

        # CPU registers
        self.a = 0
        self.f = 0
        self.b = 0
        self.c = 0
        self.d = 0
        self.e = 0
        self.h = 0
        self.l = 0
        self.pc = 0x0100
        self.sp = 0xFFFE
        self.halted = False

        self.fb = [0] * (W * H)

    def reset(self):
        self.__init__()

    def load_rom(self, data: bytes):
        self.mem[0x0100:0x0100 + len(data)] = data
        self.pc = 0x0100

    def step(self):
        if self.halted:
            return

        op = self.mem[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF

        # Mini 6502/Z80-ish opcode set
        if op == 0x00:      # NOP
            pass
        elif op == 0x76:    # HALT
            self.halted = True
        elif op == 0x3E:    # LD A, d8
            self.a = self.mem[self.pc]
            self.pc += 1
        elif op == 0x06:    # LD B, d8
            self.b = self.mem[self.pc]
            self.pc += 1
        elif op == 0x80:    # ADD A, B
            self.a = (self.a + self.b) & 0xFF
        elif op == 0xAF:    # XOR A
            self.a ^= self.a
        elif op == 0x3C:    # INC A
            self.a = (self.a + 1) & 0xFF

        # Fake memory activity (for visuals)
        self.mem[0xC000 + (self.pc % 0x1FFF)] = self.a

    def render(self):
        t = int(time.time() * 10)

        for y in range(H):
            ty = y >> 3
            for x in range(W):
                tx = x >> 3
                # DeepSeek pattern: register influence + time
                v = (tx ^ ty ^ self.a ^ t) & 3
                self.fb[x + y * W] = 1 if v == 0 else 0

        return self.fb

    def frame(self):
        for _ in range(2500):
            self.step()
        return self.render()


# ============================================================
# 🪟 DEEPSEEK UI (Black + Blue)
# ============================================================
class DeepSeekEmu:
    def __init__(self, root):
        self.root = root
        self.core = DeepSeekGB()
        self.running = False

        self.root.title("DeepSeek's GameBoy Emulator v0.1 — AC + DeepSeek")
        self.root.geometry("960x660")
        self.root.configure(bg=BG)

        # Screen
        self.canvas = tk.Canvas(
            root,
            width=W * SCALE,
            height=H * SCALE,
            bg="black",
            highlightthickness=2,
            highlightbackground=ACCENT
        )
        self.canvas.pack(padx=10, pady=10)

        # Control bar
        bar = tk.Frame(root, bg=PANEL)
        bar.pack(fill="x")

        btn_style = {
            "bg": "#0a0a0a",
            "fg": ACCENT,
            "activebackground": "#001a2a",
            "activeforeground": "#00aaff",
            "relief": tk.RAISED,
            "bd": 2,
            "font": ("Consolas", 10, "bold"),
            "width": 12
        }

        tk.Button(bar, text="Load ROM", command=self.load, **btn_style).pack(side="left", padx=2, pady=5)
        tk.Button(bar, text="Run", command=self.run, **btn_style).pack(side="left", padx=2, pady=5)
        tk.Button(bar, text="Pause", command=self.pause, **btn_style).pack(side="left", padx=2, pady=5)
        tk.Button(bar, text="Reset", command=self.reset, **btn_style).pack(side="left", padx=2, pady=5)

        self.status = tk.Label(
            root,
            text="DeepSeek Ready — Load a ROM",
            bg=BG,
            fg=ACCENT,
            font=("Consolas", 9)
        )
        self.status.pack(pady=5)

        self.loop()

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("GameBoy ROM", "*.gb *.gbc")])
        if not path:
            return
        with open(path, "rb") as f:
            self.core.load_rom(f.read())
        self.status.config(text="ROM loaded — DeepSeek analyzing...")

    def run(self):
        self.running = True
        self.status.config(text="Running — DeepSeek mode active")

    def pause(self):
        self.running = False
        self.status.config(text="Paused — DeepSeek idle")

    def reset(self):
        self.core.reset()
        self.status.config(text="Reset — DeepSeek rebooting")

    def draw(self, fb):
        self.canvas.delete("all")
        for y in range(H):
            for x in range(W):
                if fb[x + y * W]:
                    self.canvas.create_rectangle(
                        x * SCALE,
                        y * SCALE,
                        (x + 1) * SCALE,
                        (y + 1) * SCALE,
                        fill=ACCENT,
                        outline=""
                    )

    def loop(self):
        if self.running:
            fb = self.core.frame()
            self.draw(fb)
        self.root.after(16, self.loop)


# ============================================================
# 🚀 MAIN
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    DeepSeekEmu(root)
    root.mainloop()
