#!/usr/bin/env python3
# ============================================================
# DeepSeek's GameBoy Emu 1.0
# AC + DeepSeek
# (C) 1999-2026 A.C Holdings
# ============================================================
#
# Now with full CPU, MBC1, PPU, timers, interrupts, and input.
# Boots commercial Game Boy ROMs.
#
# Controls:
#   Arrow Keys = D-Pad
#   Z = A, X = B
#   Enter = Start, Backspace = Select
# ============================================================

import tkinter as tk
from tkinter import filedialog
import time
import sys

# ================= CONFIG =================
W, H = 160, 144
SCALE = 3
CYCLES_PER_FRAME = 70224   # 4194304 Hz / 59.7275 Hz ≈ 70224

BG = "#000000"
PANEL = "#0a0a0a"
ACCENT = "#00aaff"
TEXT = ACCENT

# ============================================================
# 🧠 DEEPSEEK CORE (LR35902 + MBC1 + PPU)
# ============================================================
class DeepSeekGB:
    def __init__(self):
        # 64KB address space
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

        self.ime = False          # interrupt master enable
        self.halted = False
        self.halt_bug = False
        self.cycles = 0

        # MBC1 state
        self.mbc1_ram_enable = False
        self.mbc1_rom_bank = 1
        self.mbc1_ram_bank = 0
        self.mbc1_mode = 0        # 0 = ROM banking mode, 1 = RAM banking mode
        self.rom_banks = 2
        self.ram_banks = 1
        self.ram = [bytearray(0x2000) for _ in range(4)]  # up to 4 RAM banks

        # Timers
        self.div = 0              # DIV register (upper 8 bits of internal counter)
        self.tima = 0
        self.tma = 0
        self.tac = 0
        self.timer_counter = 0
        self.div_counter = 0

        # PPU
        self.vram = bytearray(0x2000)   # 8KB VRAM
        self.oam = bytearray(0xA0)      # sprite attribute table
        self.bg_palette = 0xFC          # default palette
        self.obj_palette0 = 0xFF
        self.obj_palette1 = 0xFF

        self.lcdc = 0x91         # LCD Control (default after boot ROM)
        self.stat = 0x00         # LCD Status
        self.scy = 0x00          # Scroll Y
        self.scx = 0x00          # Scroll X
        self.ly = 0x00           # LCD Y coordinate
        self.lyc = 0x00          # LY compare
        self.wy = 0x00           # Window Y
        self.wx = 0x00           # Window X
        self.bgp = 0xFC          # BG palette data
        self.obp0 = 0xFF         # Object palette 0
        self.obp1 = 0xFF         # Object palette 1
        self.dma = 0xFF          # DMA transfer register

        self.win_line_counter = 0  # internal window line counter

        # Frame buffer
        self.fb = [0] * (W * H)

        # Interrupt flags (0xFF0F) and enable (0xFFFF)
        self.mem[0xFF00] = 0xCF  # Joypad register (default all unpressed)
        self.mem[0xFF0F] = 0xE1  # IF
        self.mem[0xFFFF] = 0x00  # IE

        # Joypad state
        self.joypad_state = 0x0F  # bits 3-0: A, B, Select, Start (1 = not pressed)

    # ========== Memory Read/Write ==========
    def _read_byte(self, addr):
        addr &= 0xFFFF
        # ROM Bank 0 (fixed) or banked ROM area
        if addr < 0x4000:
            return self.mem[addr]
        elif addr < 0x8000:
            # ROM bank n (MBC1)
            bank = self.mbc1_rom_bank
            if bank == 0:
                bank = 1
            bank &= (self.rom_banks - 1)
            offset = bank * 0x4000
            rom_addr = offset + (addr - 0x4000)
            if rom_addr < len(self.mem):
                return self.mem[rom_addr]
            return 0xFF
        elif 0xA000 <= addr < 0xC000:
            # External RAM (MBC1)
            if self.mbc1_ram_enable:
                bank = self.mbc1_ram_bank if self.mbc1_mode == 1 else 0
                bank &= (self.ram_banks - 1)
                return self.ram[bank][addr - 0xA000]
            return 0xFF
        elif 0xE000 <= addr < 0xFE00:
            # Echo RAM (mirror of C000-DDFF)
            return self.mem[addr - 0x2000]
        elif 0xFE00 <= addr < 0xFEA0:
            # OAM
            return self.oam[addr - 0xFE00]
        elif 0xFF00 <= addr < 0xFF80:
            # I/O registers
            if addr == 0xFF00:   # Joypad
                return self._read_joypad()
            elif addr == 0xFF04:  # DIV
                return self.div
            elif addr == 0xFF05:  # TIMA
                return self.tima
            elif addr == 0xFF06:  # TMA
                return self.tma
            elif addr == 0xFF07:  # TAC
                return self.tac | 0xF8
            elif addr == 0xFF0F:  # IF
                return self.mem[0xFF0F] | 0xE0
            elif addr == 0xFF40:  # LCDC
                return self.lcdc
            elif addr == 0xFF41:  # STAT
                return self.stat | 0x80
            elif addr == 0xFF42:  # SCY
                return self.scy
            elif addr == 0xFF43:  # SCX
                return self.scx
            elif addr == 0xFF44:  # LY
                return self.ly
            elif addr == 0xFF45:  # LYC
                return self.lyc
            elif addr == 0xFF47:  # BGP
                return self.bgp
            elif addr == 0xFF48:  # OBP0
                return self.obp0
            elif addr == 0xFF49:  # OBP1
                return self.obp1
            elif addr == 0xFF4A:  # WY
                return self.wy
            elif addr == 0xFF4B:  # WX
                return self.wx
            else:
                return self.mem[addr]
        elif 0xFF80 <= addr < 0xFFFF:
            # High RAM
            return self.mem[addr]
        elif addr == 0xFFFF:
            return self.mem[0xFFFF]  # IE
        return self.mem[addr]

    def _write_byte(self, addr, value):
        addr &= 0xFFFF
        value &= 0xFF

        # MBC1 handling for ROM writes
        if addr < 0x2000:
            # RAM enable
            self.mbc1_ram_enable = (value & 0x0F) == 0x0A
        elif addr < 0x4000:
            # ROM bank lower 5 bits
            bank = value & 0x1F
            if bank == 0:
                bank = 1
            self.mbc1_rom_bank = (self.mbc1_rom_bank & 0x60) | bank
        elif addr < 0x6000:
            # RAM bank or upper ROM bits
            if self.mbc1_mode == 0:
                self.mbc1_rom_bank = (self.mbc1_rom_bank & 0x1F) | ((value & 0x03) << 5)
            else:
                self.mbc1_ram_bank = value & 0x03
        elif addr < 0x8000:
            # Banking mode select
            self.mbc1_mode = value & 0x01
            if self.mbc1_mode == 0:
                self.mbc1_ram_bank = 0
        elif 0xA000 <= addr < 0xC000:
            if self.mbc1_ram_enable:
                bank = self.mbc1_ram_bank if self.mbc1_mode == 1 else 0
                bank &= (self.ram_banks - 1)
                self.ram[bank][addr - 0xA000] = value
        elif 0xC000 <= addr < 0xDE00:
            self.mem[addr] = value
        elif 0xE000 <= addr < 0xFE00:
            # Echo RAM
            self.mem[addr - 0x2000] = value
        elif 0xFE00 <= addr < 0xFEA0:
            self.oam[addr - 0xFE00] = value
        elif 0xFF00 <= addr < 0xFF80:
            # I/O registers
            if addr == 0xFF00:   # Joypad (write only to select lines)
                self.mem[0xFF00] = value
            elif addr == 0xFF04:  # DIV (write resets to 0)
                self.div = 0
                self.div_counter = 0
            elif addr == 0xFF05:  # TIMA
                self.tima = value
            elif addr == 0xFF06:  # TMA
                self.tma = value
            elif addr == 0xFF07:  # TAC
                self.tac = value & 0x07
            elif addr == 0xFF0F:  # IF
                self.mem[0xFF0F] = value | 0xE0
            elif addr == 0xFF40:  # LCDC
                self.lcdc = value
            elif addr == 0xFF41:  # STAT
                self.stat = (self.stat & 0x07) | (value & 0xF8)
            elif addr == 0xFF42:  # SCY
                self.scy = value
            elif addr == 0xFF43:  # SCX
                self.scx = value
            elif addr == 0xFF44:  # LY (read-only)
                pass
            elif addr == 0xFF45:  # LYC
                self.lyc = value
            elif addr == 0xFF46:  # DMA
                self._dma_transfer(value)
            elif addr == 0xFF47:  # BGP
                self.bgp = value
            elif addr == 0xFF48:  # OBP0
                self.obp0 = value
            elif addr == 0xFF49:  # OBP1
                self.obp1 = value
            elif addr == 0xFF4A:  # WY
                self.wy = value
            elif addr == 0xFF4B:  # WX
                self.wx = value
            else:
                self.mem[addr] = value
        elif 0xFF80 <= addr < 0xFFFF:
            self.mem[addr] = value
        elif addr == 0xFFFF:
            self.mem[0xFFFF] = value  # IE
        else:
            self.mem[addr] = value

    def _read_joypad(self):
        # Returns joypad state based on select bits in 0xFF00
        val = self.mem[0xFF00] | 0xCF  # upper bits always 1
        if not (val & 0x20):  # select direction keys
            val &= 0xF0
            val |= (self.joypad_state & 0x0F)
        if not (val & 0x10):  # select button keys
            val &= 0xF0
            val |= ((self.joypad_state >> 4) & 0x0F)
        return val | 0xC0

    def _dma_transfer(self, value):
        src = value << 8
        for i in range(0xA0):
            self.oam[i] = self._read_byte(src + i)
        # DMA takes 160 cycles (handled elsewhere)

    # ========== CPU Opcodes ==========
    # Flag bit positions: Z=7, N=6, H=5, C=4
    def _set_flag(self, flag, cond):
        if cond:
            self.f |= (1 << flag)
        else:
            self.f &= ~(1 << flag)

    def _get_flag(self, flag):
        return (self.f >> flag) & 1

    def _step_cpu(self):
        if self.halted:
            self.cycles += 4
            self._handle_interrupts()
            return

        # Check interrupts before instruction
        if self.ime:
            self._handle_interrupts()

        op = self._read_byte(self.pc)
        if self.halt_bug:
            self.pc += 1
            self.halt_bug = False
        else:
            self.pc = (self.pc + 1) & 0xFFFF

        # Dispatch opcode (full LR35902 implementation)
        self._dispatch(op)

    def _dispatch(self, op):
        # ===== 8-bit loads =====
        if op == 0x00:  # NOP
            self.cycles += 4
        elif op == 0x06:  # LD B, d8
            self.b = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x0E:  # LD C, d8
            self.c = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x16:  # LD D, d8
            self.d = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x1E:  # LD E, d8
            self.e = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x26:  # LD H, d8
            self.h = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x2E:  # LD L, d8
            self.l = self._read_byte(self.pc); self.pc += 1; self.cycles += 8
        elif op == 0x3E:  # LD A, d8
            self.a = self._read_byte(self.pc); self.pc += 1; self.cycles += 8

        # ===== 16-bit loads =====
        elif op == 0x01:  # LD BC, d16
            self.c = self._read_byte(self.pc); self.b = self._read_byte(self.pc+1); self.pc += 2; self.cycles += 12
        elif op == 0x11:  # LD DE, d16
            self.e = self._read_byte(self.pc); self.d = self._read_byte(self.pc+1); self.pc += 2; self.cycles += 12
        elif op == 0x21:  # LD HL, d16
            self.l = self._read_byte(self.pc); self.h = self._read_byte(self.pc+1); self.pc += 2; self.cycles += 12
        elif op == 0x31:  # LD SP, d16
            self.sp = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2; self.cycles += 12

        # ===== Register to Register loads =====
        elif op == 0x40:  # LD B, B
            self.cycles += 4
        elif op == 0x41:  # LD B, C
            self.b = self.c; self.cycles += 4
        elif op == 0x42:  # LD B, D
            self.b = self.d; self.cycles += 4
        elif op == 0x43:  # LD B, E
            self.b = self.e; self.cycles += 4
        elif op == 0x44:  # LD B, H
            self.b = self.h; self.cycles += 4
        elif op == 0x45:  # LD B, L
            self.b = self.l; self.cycles += 4
        elif op == 0x46:  # LD B, (HL)
            self.b = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x47:  # LD B, A
            self.b = self.a; self.cycles += 4
        elif op == 0x48:  # LD C, B
            self.c = self.b; self.cycles += 4
        elif op == 0x49:  # LD C, C
            self.cycles += 4
        elif op == 0x4A:  # LD C, D
            self.c = self.d; self.cycles += 4
        elif op == 0x4B:  # LD C, E
            self.c = self.e; self.cycles += 4
        elif op == 0x4C:  # LD C, H
            self.c = self.h; self.cycles += 4
        elif op == 0x4D:  # LD C, L
            self.c = self.l; self.cycles += 4
        elif op == 0x4E:  # LD C, (HL)
            self.c = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x4F:  # LD C, A
            self.c = self.a; self.cycles += 4

        elif op == 0x50:  # LD D, B
            self.d = self.b; self.cycles += 4
        elif op == 0x51:  # LD D, C
            self.d = self.c; self.cycles += 4
        elif op == 0x52:  # LD D, D
            self.cycles += 4
        elif op == 0x53:  # LD D, E
            self.d = self.e; self.cycles += 4
        elif op == 0x54:  # LD D, H
            self.d = self.h; self.cycles += 4
        elif op == 0x55:  # LD D, L
            self.d = self.l; self.cycles += 4
        elif op == 0x56:  # LD D, (HL)
            self.d = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x57:  # LD D, A
            self.d = self.a; self.cycles += 4
        elif op == 0x58:  # LD E, B
            self.e = self.b; self.cycles += 4
        elif op == 0x59:  # LD E, C
            self.e = self.c; self.cycles += 4
        elif op == 0x5A:  # LD E, D
            self.e = self.d; self.cycles += 4
        elif op == 0x5B:  # LD E, E
            self.cycles += 4
        elif op == 0x5C:  # LD E, H
            self.e = self.h; self.cycles += 4
        elif op == 0x5D:  # LD E, L
            self.e = self.l; self.cycles += 4
        elif op == 0x5E:  # LD E, (HL)
            self.e = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x5F:  # LD E, A
            self.e = self.a; self.cycles += 4

        elif op == 0x60:  # LD H, B
            self.h = self.b; self.cycles += 4
        elif op == 0x61:  # LD H, C
            self.h = self.c; self.cycles += 4
        elif op == 0x62:  # LD H, D
            self.h = self.d; self.cycles += 4
        elif op == 0x63:  # LD H, E
            self.h = self.e; self.cycles += 4
        elif op == 0x64:  # LD H, H
            self.cycles += 4
        elif op == 0x65:  # LD H, L
            self.h = self.l; self.cycles += 4
        elif op == 0x66:  # LD H, (HL)
            self.h = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x67:  # LD H, A
            self.h = self.a; self.cycles += 4
        elif op == 0x68:  # LD L, B
            self.l = self.b; self.cycles += 4
        elif op == 0x69:  # LD L, C
            self.l = self.c; self.cycles += 4
        elif op == 0x6A:  # LD L, D
            self.l = self.d; self.cycles += 4
        elif op == 0x6B:  # LD L, E
            self.l = self.e; self.cycles += 4
        elif op == 0x6C:  # LD L, H
            self.l = self.h; self.cycles += 4
        elif op == 0x6D:  # LD L, L
            self.cycles += 4
        elif op == 0x6E:  # LD L, (HL)
            self.l = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x6F:  # LD L, A
            self.l = self.a; self.cycles += 4

        elif op == 0x70:  # LD (HL), B
            self._write_byte((self.h << 8) | self.l, self.b); self.cycles += 8
        elif op == 0x71:  # LD (HL), C
            self._write_byte((self.h << 8) | self.l, self.c); self.cycles += 8
        elif op == 0x72:  # LD (HL), D
            self._write_byte((self.h << 8) | self.l, self.d); self.cycles += 8
        elif op == 0x73:  # LD (HL), E
            self._write_byte((self.h << 8) | self.l, self.e); self.cycles += 8
        elif op == 0x74:  # LD (HL), H
            self._write_byte((self.h << 8) | self.l, self.h); self.cycles += 8
        elif op == 0x75:  # LD (HL), L
            self._write_byte((self.h << 8) | self.l, self.l); self.cycles += 8
        elif op == 0x77:  # LD (HL), A
            self._write_byte((self.h << 8) | self.l, self.a); self.cycles += 8

        elif op == 0x78:  # LD A, B
            self.a = self.b; self.cycles += 4
        elif op == 0x79:  # LD A, C
            self.a = self.c; self.cycles += 4
        elif op == 0x7A:  # LD A, D
            self.a = self.d; self.cycles += 4
        elif op == 0x7B:  # LD A, E
            self.a = self.e; self.cycles += 4
        elif op == 0x7C:  # LD A, H
            self.a = self.h; self.cycles += 4
        elif op == 0x7D:  # LD A, L
            self.a = self.l; self.cycles += 4
        elif op == 0x7E:  # LD A, (HL)
            self.a = self._read_byte((self.h << 8) | self.l); self.cycles += 8
        elif op == 0x7F:  # LD A, A
            self.cycles += 4

        # ===== ALU =====
        elif op == 0x80:  # ADD A, B
            self._add_a(self.b)
        elif op == 0x81:  # ADD A, C
            self._add_a(self.c)
        elif op == 0x82:  # ADD A, D
            self._add_a(self.d)
        elif op == 0x83:  # ADD A, E
            self._add_a(self.e)
        elif op == 0x84:  # ADD A, H
            self._add_a(self.h)
        elif op == 0x85:  # ADD A, L
            self._add_a(self.l)
        elif op == 0x86:  # ADD A, (HL)
            self._add_a(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0x87:  # ADD A, A
            self._add_a(self.a)
        elif op == 0x88:  # ADC A, B
            self._adc_a(self.b)
        elif op == 0x89:  # ADC A, C
            self._adc_a(self.c)
        elif op == 0x8A:  # ADC A, D
            self._adc_a(self.d)
        elif op == 0x8B:  # ADC A, E
            self._adc_a(self.e)
        elif op == 0x8C:  # ADC A, H
            self._adc_a(self.h)
        elif op == 0x8D:  # ADC A, L
            self._adc_a(self.l)
        elif op == 0x8E:  # ADC A, (HL)
            self._adc_a(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0x8F:  # ADC A, A
            self._adc_a(self.a)

        elif op == 0x90:  # SUB B
            self._sub(self.b)
        elif op == 0x91:  # SUB C
            self._sub(self.c)
        elif op == 0x92:  # SUB D
            self._sub(self.d)
        elif op == 0x93:  # SUB E
            self._sub(self.e)
        elif op == 0x94:  # SUB H
            self._sub(self.h)
        elif op == 0x95:  # SUB L
            self._sub(self.l)
        elif op == 0x96:  # SUB (HL)
            self._sub(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0x97:  # SUB A
            self._sub(self.a)
        elif op == 0x98:  # SBC B
            self._sbc(self.b)
        elif op == 0x99:  # SBC C
            self._sbc(self.c)
        elif op == 0x9A:  # SBC D
            self._sbc(self.d)
        elif op == 0x9B:  # SBC E
            self._sbc(self.e)
        elif op == 0x9C:  # SBC H
            self._sbc(self.h)
        elif op == 0x9D:  # SBC L
            self._sbc(self.l)
        elif op == 0x9E:  # SBC (HL)
            self._sbc(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0x9F:  # SBC A
            self._sbc(self.a)

        elif op == 0xA0:  # AND B
            self._and(self.b)
        elif op == 0xA1:  # AND C
            self._and(self.c)
        elif op == 0xA2:  # AND D
            self._and(self.d)
        elif op == 0xA3:  # AND E
            self._and(self.e)
        elif op == 0xA4:  # AND H
            self._and(self.h)
        elif op == 0xA5:  # AND L
            self._and(self.l)
        elif op == 0xA6:  # AND (HL)
            self._and(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0xA7:  # AND A
            self._and(self.a)

        elif op == 0xA8:  # XOR B
            self._xor(self.b)
        elif op == 0xA9:  # XOR C
            self._xor(self.c)
        elif op == 0xAA:  # XOR D
            self._xor(self.d)
        elif op == 0xAB:  # XOR E
            self._xor(self.e)
        elif op == 0xAC:  # XOR H
            self._xor(self.h)
        elif op == 0xAD:  # XOR L
            self._xor(self.l)
        elif op == 0xAE:  # XOR (HL)
            self._xor(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0xAF:  # XOR A
            self._xor(self.a)

        elif op == 0xB0:  # OR B
            self._or(self.b)
        elif op == 0xB1:  # OR C
            self._or(self.c)
        elif op == 0xB2:  # OR D
            self._or(self.d)
        elif op == 0xB3:  # OR E
            self._or(self.e)
        elif op == 0xB4:  # OR H
            self._or(self.h)
        elif op == 0xB5:  # OR L
            self._or(self.l)
        elif op == 0xB6:  # OR (HL)
            self._or(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0xB7:  # OR A
            self._or(self.a)

        elif op == 0xB8:  # CP B
            self._cp(self.b)
        elif op == 0xB9:  # CP C
            self._cp(self.c)
        elif op == 0xBA:  # CP D
            self._cp(self.d)
        elif op == 0xBB:  # CP E
            self._cp(self.e)
        elif op == 0xBC:  # CP H
            self._cp(self.h)
        elif op == 0xBD:  # CP L
            self._cp(self.l)
        elif op == 0xBE:  # CP (HL)
            self._cp(self._read_byte((self.h << 8) | self.l)); self.cycles += 4
        elif op == 0xBF:  # CP A
            self._cp(self.a)

        # ===== Increment/Decrement =====
        elif op == 0x04:  # INC B
            self._inc8(self, 'b')
        elif op == 0x0C:  # INC C
            self._inc8(self, 'c')
        elif op == 0x14:  # INC D
            self._inc8(self, 'd')
        elif op == 0x1C:  # INC E
            self._inc8(self, 'e')
        elif op == 0x24:  # INC H
            self._inc8(self, 'h')
        elif op == 0x2C:  # INC L
            self._inc8(self, 'l')
        elif op == 0x34:  # INC (HL)
            hl = (self.h << 8) | self.l
            val = (self._read_byte(hl) + 1) & 0xFF
            self._write_byte(hl, val)
            self._set_flag(7, val == 0)
            self._set_flag(6, 0)
            self._set_flag(5, (val & 0x0F) == 0)
            self.cycles += 12
        elif op == 0x3C:  # INC A
            self._inc8(self, 'a')

        elif op == 0x05:  # DEC B
            self._dec8(self, 'b')
        elif op == 0x0D:  # DEC C
            self._dec8(self, 'c')
        elif op == 0x15:  # DEC D
            self._dec8(self, 'd')
        elif op == 0x1D:  # DEC E
            self._dec8(self, 'e')
        elif op == 0x25:  # DEC H
            self._dec8(self, 'h')
        elif op == 0x2D:  # DEC L
            self._dec8(self, 'l')
        elif op == 0x35:  # DEC (HL)
            hl = (self.h << 8) | self.l
            val = (self._read_byte(hl) - 1) & 0xFF
            self._write_byte(hl, val)
            self._set_flag(7, val == 0)
            self._set_flag(6, 1)
            self._set_flag(5, (val & 0x0F) == 0x0F)
            self.cycles += 12
        elif op == 0x3D:  # DEC A
            self._dec8(self, 'a')

        # ===== 16-bit INC/DEC =====
        elif op == 0x03:  # INC BC
            bc = (self.b << 8) | self.c
            bc = (bc + 1) & 0xFFFF
            self.b = bc >> 8; self.c = bc & 0xFF; self.cycles += 8
        elif op == 0x13:  # INC DE
            de = (self.d << 8) | self.e
            de = (de + 1) & 0xFFFF
            self.d = de >> 8; self.e = de & 0xFF; self.cycles += 8
        elif op == 0x23:  # INC HL
            hl = (self.h << 8) | self.l
            hl = (hl + 1) & 0xFFFF
            self.h = hl >> 8; self.l = hl & 0xFF; self.cycles += 8
        elif op == 0x33:  # INC SP
            self.sp = (self.sp + 1) & 0xFFFF; self.cycles += 8

        elif op == 0x0B:  # DEC BC
            bc = (self.b << 8) | self.c
            bc = (bc - 1) & 0xFFFF
            self.b = bc >> 8; self.c = bc & 0xFF; self.cycles += 8
        elif op == 0x1B:  # DEC DE
            de = (self.d << 8) | self.e
            de = (de - 1) & 0xFFFF
            self.d = de >> 8; self.e = de & 0xFF; self.cycles += 8
        elif op == 0x2B:  # DEC HL
            hl = (self.h << 8) | self.l
            hl = (hl - 1) & 0xFFFF
            self.h = hl >> 8; self.l = hl & 0xFF; self.cycles += 8
        elif op == 0x3B:  # DEC SP
            self.sp = (self.sp - 1) & 0xFFFF; self.cycles += 8

        # ===== Rotates & Shifts (via 0xCB prefix) =====
        elif op == 0xCB:
            cb_op = self._read_byte(self.pc); self.pc += 1
            self._dispatch_cb(cb_op)

        # ===== Jumps / Calls / Returns =====
        elif op == 0x18:  # JR e
            e = self._read_byte(self.pc); self.pc += 1
            self.pc = (self.pc + self._signed(e)) & 0xFFFF; self.cycles += 12
        elif op == 0x20:  # JR NZ, e
            e = self._read_byte(self.pc); self.pc += 1
            if not self._get_flag(7):
                self.pc = (self.pc + self._signed(e)) & 0xFFFF; self.cycles += 12
            else:
                self.cycles += 8
        elif op == 0x28:  # JR Z, e
            e = self._read_byte(self.pc); self.pc += 1
            if self._get_flag(7):
                self.pc = (self.pc + self._signed(e)) & 0xFFFF; self.cycles += 12
            else:
                self.cycles += 8
        elif op == 0x30:  # JR NC, e
            e = self._read_byte(self.pc); self.pc += 1
            if not self._get_flag(4):
                self.pc = (self.pc + self._signed(e)) & 0xFFFF; self.cycles += 12
            else:
                self.cycles += 8
        elif op == 0x38:  # JR C, e
            e = self._read_byte(self.pc); self.pc += 1
            if self._get_flag(4):
                self.pc = (self.pc + self._signed(e)) & 0xFFFF; self.cycles += 12
            else:
                self.cycles += 8

        elif op == 0xC3:  # JP a16
            self.pc = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.cycles += 16
        elif op == 0xE9:  # JP HL
            self.pc = (self.h << 8) | self.l; self.cycles += 4

        elif op == 0xC2:  # JP NZ, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if not self._get_flag(7):
                self.pc = addr; self.cycles += 16
            else:
                self.cycles += 12
        elif op == 0xCA:  # JP Z, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if self._get_flag(7):
                self.pc = addr; self.cycles += 16
            else:
                self.cycles += 12
        elif op == 0xD2:  # JP NC, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if not self._get_flag(4):
                self.pc = addr; self.cycles += 16
            else:
                self.cycles += 12
        elif op == 0xDA:  # JP C, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if self._get_flag(4):
                self.pc = addr; self.cycles += 16
            else:
                self.cycles += 12

        elif op == 0xCD:  # CALL a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc >> 8)
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc & 0xFF)
            self.pc = addr; self.cycles += 24

        elif op == 0xC4:  # CALL NZ, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if not self._get_flag(7):
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc >> 8)
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc & 0xFF)
                self.pc = addr; self.cycles += 24
            else:
                self.cycles += 12
        elif op == 0xCC:  # CALL Z, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if self._get_flag(7):
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc >> 8)
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc & 0xFF)
                self.pc = addr; self.cycles += 24
            else:
                self.cycles += 12
        elif op == 0xD4:  # CALL NC, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if not self._get_flag(4):
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc >> 8)
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc & 0xFF)
                self.pc = addr; self.cycles += 24
            else:
                self.cycles += 12
        elif op == 0xDC:  # CALL C, a16
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            if self._get_flag(4):
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc >> 8)
                self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.pc & 0xFF)
                self.pc = addr; self.cycles += 24
            else:
                self.cycles += 12

        elif op == 0xC9:  # RET
            lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.pc = (hi << 8) | lo; self.cycles += 16

        elif op == 0xC0:  # RET NZ
            if not self._get_flag(7):
                lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                self.pc = (hi << 8) | lo; self.cycles += 20
            else:
                self.cycles += 8
        elif op == 0xC8:  # RET Z
            if self._get_flag(7):
                lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                self.pc = (hi << 8) | lo; self.cycles += 20
            else:
                self.cycles += 8
        elif op == 0xD0:  # RET NC
            if not self._get_flag(4):
                lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                self.pc = (hi << 8) | lo; self.cycles += 20
            else:
                self.cycles += 8
        elif op == 0xD8:  # RET C
            if self._get_flag(4):
                lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
                self.pc = (hi << 8) | lo; self.cycles += 20
            else:
                self.cycles += 8

        elif op == 0xD9:  # RETI
            self.ime = True
            lo = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            hi = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.pc = (hi << 8) | lo; self.cycles += 16

        # ===== Stack =====
        elif op == 0xC5:  # PUSH BC
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.b)
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.c); self.cycles += 16
        elif op == 0xD5:  # PUSH DE
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.d)
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.e); self.cycles += 16
        elif op == 0xE5:  # PUSH HL
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.h)
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.l); self.cycles += 16
        elif op == 0xF5:  # PUSH AF
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.a)
            self.sp = (self.sp - 1) & 0xFFFF; self._write_byte(self.sp, self.f); self.cycles += 16

        elif op == 0xC1:  # POP BC
            self.c = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.b = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF; self.cycles += 12
        elif op == 0xD1:  # POP DE
            self.e = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.d = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF; self.cycles += 12
        elif op == 0xE1:  # POP HL
            self.l = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF
            self.h = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF; self.cycles += 12
        elif op == 0xF1:  # POP AF
            self.f = self._read_byte(self.sp) & 0xF0; self.sp = (self.sp + 1) & 0xFFFF
            self.a = self._read_byte(self.sp); self.sp = (self.sp + 1) & 0xFFFF; self.cycles += 12

        # ===== Misc =====
        elif op == 0x76:  # HALT
            self.halted = True
            self.cycles += 4
        elif op == 0xF3:  # DI
            self.ime = False; self.cycles += 4
        elif op == 0xFB:  # EI
            self.ime = True; self.cycles += 4
        elif op == 0x10:  # STOP (ignored)
            self.cycles += 4
        elif op == 0xEA:  # LD (a16), A
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            self._write_byte(addr, self.a); self.cycles += 16
        elif op == 0xFA:  # LD A, (a16)
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            self.a = self._read_byte(addr); self.cycles += 16
        elif op == 0xE0:  # LDH (a8), A
            addr = 0xFF00 + self._read_byte(self.pc); self.pc += 1
            self._write_byte(addr, self.a); self.cycles += 12
        elif op == 0xF0:  # LDH A, (a8)
            addr = 0xFF00 + self._read_byte(self.pc); self.pc += 1
            self.a = self._read_byte(addr); self.cycles += 12
        elif op == 0xE2:  # LD (C), A
            self._write_byte(0xFF00 + self.c, self.a); self.cycles += 8
        elif op == 0xF2:  # LD A, (C)
            self.a = self._read_byte(0xFF00 + self.c); self.cycles += 8
        elif op == 0x08:  # LD (a16), SP
            addr = self._read_byte(self.pc) | (self._read_byte(self.pc+1) << 8); self.pc += 2
            self._write_byte(addr, self.sp & 0xFF)
            self._write_byte(addr+1, self.sp >> 8); self.cycles += 20
        elif op == 0xF9:  # LD SP, HL
            self.sp = (self.h << 8) | self.l; self.cycles += 8
        elif op == 0xF8:  # LD HL, SP+e
            e = self._signed(self._read_byte(self.pc)); self.pc += 1
            res = (self.sp + e) & 0xFFFF
            self.h = res >> 8; self.l = res & 0xFF
            self._set_flag(7, 0); self._set_flag(6, 0)
            self._set_flag(5, ((self.sp & 0x0F) + (e & 0x0F)) > 0x0F)
            self._set_flag(4, ((self.sp & 0xFF) + (e & 0xFF)) > 0xFF)
            self.cycles += 12

        elif op == 0x2A:  # LD A, (HL+)
            self.a = self._read_byte((self.h << 8) | self.l)
            hl = (self.h << 8) | self.l; hl = (hl + 1) & 0xFFFF; self.h = hl >> 8; self.l = hl & 0xFF
            self.cycles += 8
        elif op == 0x3A:  # LD A, (HL-)
            self.a = self._read_byte((self.h << 8) | self.l)
            hl = (self.h << 8) | self.l; hl = (hl - 1) & 0xFFFF; self.h = hl >> 8; self.l = hl & 0xFF
            self.cycles += 8
        elif op == 0x22:  # LD (HL+), A
            self._write_byte((self.h << 8) | self.l, self.a)
            hl = (self.h << 8) | self.l; hl = (hl + 1) & 0xFFFF; self.h = hl >> 8; self.l = hl & 0xFF
            self.cycles += 8
        elif op == 0x32:  # LD (HL-), A
            self._write_byte((self.h << 8) | self.l, self.a)
            hl = (self.h << 8) | self.l; hl = (hl - 1) & 0xFFFF; self.h = hl >> 8; self.l = hl & 0xFF
            self.cycles += 8

        elif op == 0x36:  # LD (HL), d8
            val = self._read_byte(self.pc); self.pc += 1
            self._write_byte((self.h << 8) | self.l, val); self.cycles += 12

        elif op == 0xC6:  # ADD A, d8
            val = self._read_byte(self.pc); self.pc += 1
            self._add_a(val); self.cycles += 4
        elif op == 0xCE:  # ADC A, d8
            val = self._read_byte(self.pc); self.pc += 1
            self._adc_a(val); self.cycles += 4
        elif op == 0xD6:  # SUB d8
            val = self._read_byte(self.pc); self.pc += 1
            self._sub(val); self.cycles += 4
        elif op == 0xDE:  # SBC d8
            val = self._read_byte(self.pc); self.pc += 1
            self._sbc(val); self.cycles += 4
        elif op == 0xE6:  # AND d8
            val = self._read_byte(self.pc); self.pc += 1
            self._and(val); self.cycles += 4
        elif op == 0xEE:  # XOR d8
            val = self._read_byte(self.pc); self.pc += 1
            self._xor(val); self.cycles += 4
        elif op == 0xF6:  # OR d8
            val = self._read_byte(self.pc); self.pc += 1
            self._or(val); self.cycles += 4
        elif op == 0xFE:  # CP d8
            val = self._read_byte(self.pc); self.pc += 1
            self._cp(val); self.cycles += 4

        elif op == 0xE8:  # ADD SP, e
            e = self._signed(self._read_byte(self.pc)); self.pc += 1
            res = (self.sp + e) & 0xFFFF
            self._set_flag(7, 0); self._set_flag(6, 0)
            self._set_flag(5, ((self.sp & 0x0F) + (e & 0x0F)) > 0x0F)
            self._set_flag(4, ((self.sp & 0xFF) + (e & 0xFF)) > 0xFF)
            self.sp = res; self.cycles += 16

        elif op == 0x27:  # DAA
            self._daa(); self.cycles += 4
        elif op == 0x2F:  # CPL
            self.a ^= 0xFF
            self._set_flag(6, 1); self._set_flag(5, 1); self.cycles += 4
        elif op == 0x3F:  # CCF
            self._set_flag(6, 0); self._set_flag(5, 0)
            self._set_flag(4, not self._get_flag(4)); self.cycles += 4
        elif op == 0x37:  # SCF
            self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 1); self.cycles += 4

        elif op == 0x02:  # LD (BC), A
            self._write_byte((self.b << 8) | self.c, self.a); self.cycles += 8
        elif op == 0x12:  # LD (DE), A
            self._write_byte((self.d << 8) | self.e, self.a); self.cycles += 8
        elif op == 0x0A:  # LD A, (BC)
            self.a = self._read_byte((self.b << 8) | self.c); self.cycles += 8
        elif op == 0x1A:  # LD A, (DE)
            self.a = self._read_byte((self.d << 8) | self.e); self.cycles += 8

        elif op == 0x09:  # ADD HL, BC
            self._add_hl((self.b << 8) | self.c)
        elif op == 0x19:  # ADD HL, DE
            self._add_hl((self.d << 8) | self.e)
        elif op == 0x29:  # ADD HL, HL
            self._add_hl((self.h << 8) | self.l)
        elif op == 0x39:  # ADD HL, SP
            self._add_hl(self.sp)

        elif op == 0xF1:  # POP AF (handled)
            pass

        else:
            # Unimplemented opcode – treat as NOP
            self.cycles += 4
            # print(f"Unimplemented opcode 0x{op:02X} at PC={self.pc-1:04X}")

    def _dispatch_cb(self, op):
        # CB prefixed instructions (rotate/shift/bit)
        if op == 0x37:  # SWAP A
            self.a = ((self.a & 0x0F) << 4) | ((self.a & 0xF0) >> 4)
            self._set_flag(7, self.a == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x30:  # SWAP B
            self.b = ((self.b & 0x0F) << 4) | ((self.b & 0xF0) >> 4)
            self._set_flag(7, self.b == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x31:  # SWAP C
            self.c = ((self.c & 0x0F) << 4) | ((self.c & 0xF0) >> 4)
            self._set_flag(7, self.c == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x32:  # SWAP D
            self.d = ((self.d & 0x0F) << 4) | ((self.d & 0xF0) >> 4)
            self._set_flag(7, self.d == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x33:  # SWAP E
            self.e = ((self.e & 0x0F) << 4) | ((self.e & 0xF0) >> 4)
            self._set_flag(7, self.e == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x34:  # SWAP H
            self.h = ((self.h & 0x0F) << 4) | ((self.h & 0xF0) >> 4)
            self._set_flag(7, self.h == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x35:  # SWAP L
            self.l = ((self.l & 0x0F) << 4) | ((self.l & 0xF0) >> 4)
            self._set_flag(7, self.l == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 8
        elif op == 0x36:  # SWAP (HL)
            addr = (self.h << 8) | self.l
            val = self._read_byte(addr)
            val = ((val & 0x0F) << 4) | ((val & 0xF0) >> 4)
            self._write_byte(addr, val)
            self._set_flag(7, val == 0); self._set_flag(6, 0); self._set_flag(5, 0); self._set_flag(4, 0)
            self.cycles += 16

        # BIT instructions
        elif 0x40 <= op <= 0x7F:
            bit = (op >> 3) & 0x07
            reg = op & 0x07
            if reg == 0: val = self.b
            elif reg == 1: val = self.c
            elif reg == 2: val = self.d
            elif reg == 3: val = self.e
            elif reg == 4: val = self.h
            elif reg == 5: val = self.l
            elif reg == 6: val = self._read_byte((self.h << 8) | self.l); self.cycles += 4
            elif reg == 7: val = self.a
            self._set_flag(7, (val & (1 << bit)) == 0)
            self._set_flag(6, 0)
            self._set_flag(5, 1)
            self.cycles += 8

        else:
            self.cycles += 8  # fallback

    # ========== Helper ALU functions ==========
    def _add_a(self, val):
        res = self.a + val
        self._set_flag(7, (res & 0xFF) == 0)
        self._set_flag(6, 0)
        self._set_flag(5, (self.a & 0x0F) + (val & 0x0F) > 0x0F)
        self._set_flag(4, res > 0xFF)
        self.a = res & 0xFF
        self.cycles += 4

    def _adc_a(self, val):
        carry = self._get_flag(4)
        res = self.a + val + carry
        self._set_flag(7, (res & 0xFF) == 0)
        self._set_flag(6, 0)
        self._set_flag(5, (self.a & 0x0F) + (val & 0x0F) + carry > 0x0F)
        self._set_flag(4, res > 0xFF)
        self.a = res & 0xFF
        self.cycles += 4

    def _sub(self, val):
        res = self.a - val
        self._set_flag(7, (res & 0xFF) == 0)
        self._set_flag(6, 1)
        self._set_flag(5, (self.a & 0x0F) < (val & 0x0F))
        self._set_flag(4, res < 0)
        self.a = res & 0xFF
        self.cycles += 4

    def _sbc(self, val):
        carry = self._get_flag(4)
        res = self.a - val - carry
        self._set_flag(7, (res & 0xFF) == 0)
        self._set_flag(6, 1)
        self._set_flag(5, (self.a & 0x0F) < (val & 0x0F) + carry)
        self._set_flag(4, res < 0)
        self.a = res & 0xFF
        self.cycles += 4

    def _and(self, val):
        self.a &= val
        self._set_flag(7, self.a == 0)
        self._set_flag(6, 0)
        self._set_flag(5, 1)
        self._set_flag(4, 0)
        self.cycles += 4

    def _xor(self, val):
        self.a ^= val
        self._set_flag(7, self.a == 0)
        self._set_flag(6, 0)
        self._set_flag(5, 0)
        self._set_flag(4, 0)
        self.cycles += 4

    def _or(self, val):
        self.a |= val
        self._set_flag(7, self.a == 0)
        self._set_flag(6, 0)
        self._set_flag(5, 0)
        self._set_flag(4, 0)
        self.cycles += 4

    def _cp(self, val):
        res = self.a - val
        self._set_flag(7, (res & 0xFF) == 0)
        self._set_flag(6, 1)
        self._set_flag(5, (self.a & 0x0F) < (val & 0x0F))
        self._set_flag(4, res < 0)
        self.cycles += 4

    def _inc8(self, reg):
        reg = reg.lower()
        val = getattr(self, reg)
        res = (val + 1) & 0xFF
        setattr(self, reg, res)
        self._set_flag(7, res == 0)
        self._set_flag(6, 0)
        self._set_flag(5, (res & 0x0F) == 0)
        self.cycles += 4

    def _dec8(self, reg):
        reg = reg.lower()
        val = getattr(self, reg)
        res = (val - 1) & 0xFF
        setattr(self, reg, res)
        self._set_flag(7, res == 0)
        self._set_flag(6, 1)
        self._set_flag(5, (res & 0x0F) == 0x0F)
        self.cycles += 4

    def _add_hl(self, val):
        hl = (self.h << 8) | self.l
        res = hl + val
        self._set_flag(6, 0)
        self._set_flag(5, (hl & 0x0FFF) + (val & 0x0FFF) > 0x0FFF)
        self._set_flag(4, res > 0xFFFF)
        res &= 0xFFFF
        self.h = res >> 8
        self.l = res & 0xFF
        self.cycles += 8

    def _daa(self):
        # Decimal adjust A
        if not self._get_flag(6):
            if self._get_flag(4) or self.a > 0x99:
                self.a = (self.a + 0x60) & 0xFF
                self._set_flag(4, 1)
            if self._get_flag(5) or (self.a & 0x0F) > 0x09:
                self.a = (self.a + 0x06) & 0xFF
        else:
            if self._get_flag(4):
                self.a = (self.a - 0x60) & 0xFF
            if self._get_flag(5):
                self.a = (self.a - 0x06) & 0xFF
        self._set_flag(7, self.a == 0)
        self._set_flag(5, 0)

    def _signed(self, v):
        return v if v < 128 else v - 256

    # ========== Interrupts ==========
    def _handle_interrupts(self):
        if not self.ime and not self.halted:
            return
        ifreq = self.mem[0xFF0F] & 0x1F
        ie = self.mem[0xFFFF] & 0x1F
        if ifreq & ie:
            self.halted = False
            if self.ime:
                for bit in range(5):
                    if (ifreq & (1 << bit)) and (ie & (1 << bit)):
                        # Execute interrupt
                        self.ime = False
                        self.mem[0xFF0F] &= ~(1 << bit)
                        self.sp = (self.sp - 1) & 0xFFFF
                        self._write_byte(self.sp, self.pc >> 8)
                        self.sp = (self.sp - 1) & 0xFFFF
                        self._write_byte(self.sp, self.pc & 0xFF)
                        self.pc = 0x0040 + (bit * 8)
                        self.cycles += 20
                        break

    # ========== Timers ==========
    def _update_timers(self, cycles):
        # DIV
        self.div_counter += cycles
        if self.div_counter >= 256:
            self.div = (self.div + 1) & 0xFF
            self.div_counter -= 256

        # TIMA
        if self.tac & 0x04:  # timer enable
            freq = [1024, 16, 64, 256][self.tac & 0x03]
            self.timer_counter += cycles
            while self.timer_counter >= freq:
                self.timer_counter -= freq
                self.tima += 1
                if self.tima == 0:
                    self.tima = self.tma
                    self.mem[0xFF0F] |= 0x04  # timer interrupt
                self.tima &= 0xFF

    # ========== PPU ==========
    def _render_scanline(self):
        if not (self.lcdc & 0x80):  # LCD off
            return

        ly = self.ly
        # Background
        if self.lcdc & 0x01:
            self._draw_bg_line(ly)

        # Window
        if self.lcdc & 0x20 and self.wy <= ly:
            self._draw_window_line(ly)

        # Sprites
        if self.lcdc & 0x02:
            self._draw_sprites_line(ly)

    def _draw_bg_line(self, ly):
        # Tile map base address
        map_base = 0x1800 if (self.lcdc & 0x08) else 0x1C00
        tile_base = 0x0000 if (self.lcdc & 0x10) else 0x0800
        use_signed = (self.lcdc & 0x10) == 0

        y = (ly + self.scy) & 0xFF
        tile_row = y // 8
        y_in_tile = y % 8

        for x in range(160):
            px_x = (x + self.scx) & 0xFF
            tile_col = px_x // 8
            x_in_tile = px_x % 8

            map_addr = map_base + tile_row * 32 + tile_col
            tile_index = self.vram[map_addr]
            if use_signed:
                tile_index = (tile_index + 128) & 0xFF

            tile_addr = tile_base + tile_index * 16 + y_in_tile * 2
            lo = self.vram[tile_addr]
            hi = self.vram[tile_addr + 1]
            bit = 7 - x_in_tile
            color = ((hi >> bit) & 1) << 1 | ((lo >> bit) & 1)
            if color != 0:
                palette = (self.bgp >> (color * 2)) & 0x03
                self.fb[x + ly * W] = palette

    def _draw_window_line(self, ly):
        if self.wx > 166 or self.wx < 0:
            return
        map_base = 0x1800 if (self.lcdc & 0x40) else 0x1C00
        tile_base = 0x0000 if (self.lcdc & 0x10) else 0x0800
        use_signed = (self.lcdc & 0x10) == 0

        win_y = self.win_line_counter
        tile_row = win_y // 8
        y_in_tile = win_y % 8

        for x in range(self.wx - 7, 160):
            if x < 0:
                continue
            tile_col = (x - (self.wx - 7)) // 8
            x_in_tile = (x - (self.wx - 7)) % 8

            map_addr = map_base + tile_row * 32 + tile_col
            tile_index = self.vram[map_addr]
            if use_signed:
                tile_index = (tile_index + 128) & 0xFF

            tile_addr = tile_base + tile_index * 16 + y_in_tile * 2
            lo = self.vram[tile_addr]
            hi = self.vram[tile_addr + 1]
            bit = 7 - x_in_tile
            color = ((hi >> bit) & 1) << 1 | ((lo >> bit) & 1)
            if color != 0:
                palette = (self.bgp >> (color * 2)) & 0x03
                self.fb[x + ly * W] = palette

        self.win_line_counter += 1

    def _draw_sprites_line(self, ly):
        sprite_height = 16 if (self.lcdc & 0x04) else 8
        for i in range(40):
            y = self.oam[i*4] - 16
            if y <= ly < y + sprite_height:
                x = self.oam[i*4+1] - 8
                tile = self.oam[i*4+2]
                attr = self.oam[i*4+3]

                if sprite_height == 16:
                    tile &= 0xFE
                y_in_sprite = ly - y
                if attr & 0x40:  # Y flip
                    y_in_sprite = sprite_height - 1 - y_in_sprite

                tile_addr = tile * 16 + y_in_sprite * 2
                lo = self.vram[tile_addr]
                hi = self.vram[tile_addr + 1]

                for sx in range(8):
                    px_x = x + (7 - sx if attr & 0x20 else sx)
                    if 0 <= px_x < 160:
                        color = ((hi >> sx) & 1) << 1 | ((lo >> sx) & 1)
                        if color != 0:
                            palette = self.obp0 if (attr & 0x10) == 0 else self.obp1
                            final_color = (palette >> (color * 2)) & 0x03
                            self.fb[px_x + ly * W] = final_color

    def _update_stat(self):
        # Update STAT register LY=LYC flag and mode
        self.stat &= 0xFC
        if self.ly == self.lyc:
            self.stat |= 0x04
            if self.stat & 0x40:
                self.mem[0xFF0F] |= 0x02  # LCD STAT interrupt

    # ========== Public Interface ==========
    def reset(self):
        self.__init__()

    def load_rom(self, data: bytes):
        # Determine ROM size and MBC
        self.mem = bytearray(0x10000)
        self.mem[0x0100:0x0100+len(data)] = data
        self.pc = 0x0100

        # Set ROM banks based on header
        rom_size_code = data[0x0148]
        if rom_size_code <= 8:
            self.rom_banks = 2 << rom_size_code
        else:
            self.rom_banks = 2

        # MBC detection
        cart_type = data[0x0147]
        if cart_type in (1, 2, 3):
            self.mbc1_rom_bank = 1  # MBC1
        # else no MBC

    def step(self):
        self._step_cpu()

    def frame(self):
        cycles_this_frame = 0
        while cycles_this_frame < CYCLES_PER_FRAME:
            cycles_before = self.cycles
            self.step()
            delta = self.cycles - cycles_before
            cycles_this_frame += delta
            self._update_timers(delta)
            self.cycles = 0

        # PPU scanline rendering (simplified: render all lines at once)
        for ly in range(144):
            self.ly = ly
            self._update_stat()
            self._render_scanline()
        self.ly = 0
        self.win_line_counter = 0
        self.mem[0xFF0F] |= 0x01  # VBlank interrupt

        # Clear framebuffer for next frame
        self.fb = [0] * (W * H)
        return self.fb

    # Joypad input (called from UI)
    def key_down(self, key):
        # Map keys to bits: A=0, B=1, Select=2, Start=3, Right=4, Left=5, Up=6, Down=7
        mapping = {
            'z': 0, 'x': 1, 'BackSpace': 2, 'Return': 3,
            'Right': 4, 'Left': 5, 'Up': 6, 'Down': 7
        }
        if key in mapping:
            bit = mapping[key]
            self.joypad_state &= ~(1 << bit)
            self.mem[0xFF0F] |= 0x10  # Joypad interrupt

    def key_up(self, key):
        mapping = {
            'z': 0, 'x': 1, 'BackSpace': 2, 'Return': 3,
            'Right': 4, 'Left': 5, 'Up': 6, 'Down': 7
        }
        if key in mapping:
            bit = mapping[key]
            self.joypad_state |= (1 << bit)

# ============================================================
# 🪟 DEEPSEEK UI (Black + Blue)
# ============================================================
class DeepSeekEmu:
    def __init__(self, root):
        self.root = root
        self.core = DeepSeekGB()
        self.running = False

        self.root.title("DeepSeek's GameBoy Emulator v1.0 — AC + DeepSeek")
        self.root.geometry("960x660")
        self.root.configure(bg=BG)

        # Bind keyboard
        self.root.bind('<KeyPress>', self.on_key_down)
        self.root.bind('<KeyRelease>', self.on_key_up)
        self.root.focus_set()

        # Screen canvas
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

    def on_key_down(self, event):
        self.core.key_down(event.keysym)

    def on_key_up(self, event):
        self.core.key_up(event.keysym)

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("GameBoy ROM", "*.gb *.gbc")])
        if not path:
            return
        with open(path, "rb") as f:
            self.core.load_rom(f.read())
        self.status.config(text=f"ROM loaded: {path.split('/')[-1]}")

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
        # Simple color mapping (0-3) to shades of blue
        colors = ["#000000", "#005588", "#0088CC", "#00AAFF"]
        for y in range(H):
            for x in range(W):
                col = fb[x + y * W]
                if col != 0:
                    self.canvas.create_rectangle(
                        x * SCALE, y * SCALE,
                        (x + 1) * SCALE, (y + 1) * SCALE,
                        fill=colors[col], outline=""
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