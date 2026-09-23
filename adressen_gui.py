"""
TEXMA — Lieferadressen pflegen
===============================

Oberfläche für den Innendienst, um feste Lieferadressen je Shop und
Lieferort zu hinterlegen. Diese Adressen landen beim nächsten Import im
<Delivery>-Block der WEX-Datei.

Bewusst getrennt von den Zugangsdaten (zugang.yaml): die API-Schlüssel
werden hier weder angezeigt noch verändert. Dieses Tool nutzt aus der
Konfiguration ausschließlich die Shop-Namen.

Bauen:
    python -m PyInstaller --onefile --windowed --name Lieferadressen adressen_gui.py
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

import yaml

import woo_to_cdh as w   # gemeinsame Konfig-Ladefunktion (Welle 2)


# --- Ablage -----------------------------------------------------------------

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

ADDRESSES_PATH = BASE_DIR / "lieferadressen.yaml"
BACKUP_DIR = BASE_DIR / "Backup"

# TEXMA-Farben
NAVY = "#0E1C36"
GREEN = "#34FF67"
DARK_GREEN = "#386A4E"
MINT = "#D4FFDF"

FIELDS = [
    ("name1",    "Firma"),
    ("name2",    "z. Hd. / Zusatz"),
    ("street",   "Straße und Nr."),
    ("postcode", "PLZ"),
    ("city",     "Ort"),
    ("country",  "Land (DE, AT, CH)"),
]

HEADER = """# Feste Lieferadressen je Shop und Lieferort
# ==========================================
#
# Gepflegt über das Tool "Lieferadressen". Enthält keine Zugangsdaten.
# Der Lieferort muss der Versandart im Shop entsprechen.
# Lieferorte ohne Eintrag behalten die Adresse aus der Bestellung.
#
# Zuletzt gespeichert: {stamp}

"""


# --- Datenhaltung -----------------------------------------------------------

def load_shop_names() -> list:
    """Shop-Namen aus der Konfiguration (einstellungen.yaml + zugang.yaml oder
    config.yaml als Rückfall). Zugangsdaten werden hier weder angezeigt noch
    gespeichert — es geht nur um die Namen für die linke Spalte."""
    try:
        cfg, _quelle = w.load_config()
    except Exception:  # noqa: BLE001  (fehlt/kaputt -> leere Liste, Tool startet trotzdem)
        return []
    return [str(s.get("name")) for s in (cfg.get("shops") or [])
            if s.get("name")]


def load_addresses() -> dict:
    if not ADDRESSES_PATH.exists():
        return {}
    try:
        with ADDRESSES_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        messagebox.showerror(
            "Datei fehlerhaft",
            f"lieferadressen.yaml konnte nicht gelesen werden:\n\n{e}\n\n"
            "Das Tool startet mit leerer Liste. Bitte NICHT speichern, "
            "sonst gehen die bisherigen Adressen verloren — erst Jannik "
            "Bescheid geben."
        )
        return {}


def save_addresses(data: dict) -> None:
    """Schreibt die Datei und legt vorher eine Sicherung an."""
    if ADDRESSES_PATH.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(ADDRESSES_PATH,
                     BACKUP_DIR / f"lieferadressen_{stamp}.yaml")

    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=True,
                          default_flow_style=False, indent=2)
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    with ADDRESSES_PATH.open("w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER.format(stamp=stamp))
        f.write(body)


# --- Oberfläche -------------------------------------------------------------

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("TEXMA — Lieferadressen")
        self.geometry("900x620")
        self.minsize(820, 560)
        self.configure(bg="white")

        self.data = load_addresses()
        self.shop_names = load_shop_names()
        self.current_shop = None
        self.current_ort = None
        self.dirty = False

        self._build_styles()
        self._build_layout()
        self._fill_shops()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- Aufbau --------------------------------------------------------------

    def _build_styles(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background="white")
        st.configure("TLabel", background="white", foreground=NAVY)
        st.configure("Head.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("Sub.TLabel", foreground=DARK_GREEN)
        st.configure("TButton", padding=6)
        st.configure("TEntry", fieldbackground="white")

    def _build_layout(self):
        kopf = ttk.Frame(self, padding=(16, 12, 16, 8))
        kopf.pack(fill="x")
        ttk.Label(kopf, text="Lieferadressen", style="Head.TLabel").pack(anchor="w")
        ttk.Label(kopf, style="Sub.TLabel",
                  text="Feste Versandadressen je Shop und Lieferort. "
                       "Wirken beim nächsten Import.").pack(anchor="w")
        tk.Frame(self, height=2, bg=GREEN).pack(fill="x", padx=16)

        mitte = ttk.Frame(self, padding=16)
        mitte.pack(fill="both", expand=True)

        # Linke Spalte: Shop und Lieferorte
        links = ttk.Frame(mitte)
        links.pack(side="left", fill="y", padx=(0, 16))

        ttk.Label(links, text="Shop").pack(anchor="w")
        self.shop_box = ttk.Combobox(links, state="readonly", width=28)
        self.shop_box.pack(anchor="w", pady=(2, 12))
        self.shop_box.bind("<<ComboboxSelected>>", self._on_shop_change)

        ttk.Label(links, text="Lieferorte").pack(anchor="w")
        self.ort_list = tk.Listbox(links, width=30, height=16,
                                   exportselection=False,
                                   highlightthickness=1,
                                   highlightbackground="#D0D5DD",
                                   selectbackground=MINT,
                                   selectforeground=NAVY,
                                   activestyle="none")
        self.ort_list.pack(anchor="w", pady=(2, 8))
        self.ort_list.bind("<<ListboxSelect>>", self._on_ort_change)

        btns = ttk.Frame(links)
        btns.pack(anchor="w")
        ttk.Button(btns, text="Neu", command=self._neuer_ort).pack(side="left")
        ttk.Button(btns, text="Löschen",
                   command=self._ort_loeschen).pack(side="left", padx=6)

        # Rechte Spalte: Formular
        rechts = ttk.Frame(mitte)
        rechts.pack(side="left", fill="both", expand=True)

        self.form_titel = ttk.Label(rechts, text="Kein Lieferort gewählt",
                                    font=("Segoe UI", 11, "bold"))
        self.form_titel.pack(anchor="w", pady=(0, 10))

        self.entries = {}
        for key, label in FIELDS:
            zeile = ttk.Frame(rechts)
            zeile.pack(fill="x", pady=4)
            ttk.Label(zeile, text=label, width=18).pack(side="left")
            e = ttk.Entry(zeile)
            e.pack(side="left", fill="x", expand=True)
            e.bind("<KeyRelease>", lambda _e: self._mark_dirty())
            self.entries[key] = e

        hinweis = tk.Label(
            rechts, bg=MINT, fg=NAVY, justify="left", anchor="w",
            padx=12, pady=10, wraplength=430,
            text="Der Lieferort muss genauso heißen wie die Versandart im "
                 "Shop. Lieferorte ohne Eintrag behalten die Adresse aus "
                 "der Bestellung."
        )
        hinweis.pack(fill="x", pady=(16, 0))

        # Fußzeile
        fuss = ttk.Frame(self, padding=(16, 8, 16, 14))
        fuss.pack(fill="x")
        self.status = ttk.Label(fuss, text="", style="Sub.TLabel")
        self.status.pack(side="left")
        ttk.Button(fuss, text="Schließen",
                   command=self._on_close).pack(side="right")
        ttk.Button(fuss, text="Speichern",
                   command=self._speichern).pack(side="right", padx=8)

    # -- Datenfluss ----------------------------------------------------------

    def _fill_shops(self):
        namen = list(self.shop_names)
        for s in self.data:
            if s not in namen:
                namen.append(s)
        self.shop_box["values"] = namen
        if namen:
            self.shop_box.current(0)
            self._on_shop_change()

    def _on_shop_change(self, _evt=None):
        self._uebernehmen()
        self.current_shop = self.shop_box.get()
        self.current_ort = None
        self._fill_orte()
        self._formular_leeren()

    def _fill_orte(self):
        self.ort_list.delete(0, "end")
        for ort in sorted((self.data.get(self.current_shop) or {})):
            self.ort_list.insert("end", ort)

    def _on_ort_change(self, _evt=None):
        sel = self.ort_list.curselection()
        if not sel:
            return
        self._uebernehmen()
        self.current_ort = self.ort_list.get(sel[0])
        adr = (self.data.get(self.current_shop) or {}).get(self.current_ort, {})
        self.form_titel.config(text=self.current_ort)
        for key, _label in FIELDS:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, str(adr.get(key) or ""))

    def _formular_leeren(self):
        self.form_titel.config(text="Kein Lieferort gewählt")
        for key, _label in FIELDS:
            self.entries[key].delete(0, "end")

    def _uebernehmen(self):
        """Formularwerte in die Datenstruktur zurückschreiben."""
        if not self.current_shop or not self.current_ort:
            return
        adr = {}
        for key, _label in FIELDS:
            val = self.entries[key].get().strip()
            if val:
                adr[key] = val
        if not adr.get("country"):
            adr["country"] = "DE"
        self.data.setdefault(self.current_shop, {})[self.current_ort] = adr

    def _mark_dirty(self):
        self.dirty = True
        self.status.config(text="Ungespeicherte Änderungen")

    # -- Aktionen ------------------------------------------------------------

    def _neuer_ort(self):
        if not self.current_shop:
            return
        dlg = tk.Toplevel(self)
        dlg.title("Neuer Lieferort")
        dlg.configure(bg="white")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        ttk.Label(dlg, text="Name des Lieferorts — genau wie die "
                            "Versandart im Shop:").pack(padx=16, pady=(16, 6))
        e = ttk.Entry(dlg, width=38)
        e.pack(padx=16)
        e.focus_set()

        def ok():
            name = e.get().strip()
            if not name:
                return
            if name in (self.data.get(self.current_shop) or {}):
                messagebox.showinfo("Schon vorhanden",
                                    f"'{name}' gibt es bereits.", parent=dlg)
                return
            self.data.setdefault(self.current_shop, {})[name] = {"country": "DE"}
            self._fill_orte()
            idx = sorted(self.data[self.current_shop]).index(name)
            self.ort_list.selection_clear(0, "end")
            self.ort_list.selection_set(idx)
            self.ort_list.see(idx)
            self._on_ort_change()
            self._mark_dirty()
            dlg.destroy()

        leiste = ttk.Frame(dlg)
        leiste.pack(pady=14)
        ttk.Button(leiste, text="Anlegen", command=ok).pack(side="left", padx=4)
        ttk.Button(leiste, text="Abbrechen",
                   command=dlg.destroy).pack(side="left", padx=4)
        dlg.bind("<Return>", lambda _e: ok())

    def _ort_loeschen(self):
        if not (self.current_shop and self.current_ort):
            return
        if not messagebox.askyesno(
            "Lieferort löschen",
            f"'{self.current_ort}' wirklich löschen?\n\n"
            "Bestellungen an diesen Lieferort bekommen dann wieder die "
            "Adresse aus der Bestellung."
        ):
            return
        self.data.get(self.current_shop, {}).pop(self.current_ort, None)
        self.current_ort = None
        self._fill_orte()
        self._formular_leeren()
        self._mark_dirty()

    def _pruefen(self) -> list:
        """Meldet unvollständige Einträge, damit nichts halbfertig rausgeht."""
        fehler = []
        for shop, orte in self.data.items():
            for ort, adr in (orte or {}).items():
                fehlend = [lbl for key, lbl in FIELDS
                           if key in ("name1", "street", "postcode", "city")
                           and not str(adr.get(key) or "").strip()]
                if fehlend:
                    fehler.append(f"{shop} / {ort}: {', '.join(fehlend)}")
        return fehler

    def _speichern(self):
        self._uebernehmen()
        fehler = self._pruefen()
        if fehler:
            if not messagebox.askyesno(
                "Unvollständige Einträge",
                "Bei diesen Lieferorten fehlen Angaben:\n\n"
                + "\n".join(fehler[:10])
                + ("\n…" if len(fehler) > 10 else "")
                + "\n\nTrotzdem speichern?"
            ):
                return
        try:
            save_addresses(self.data)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Speichern fehlgeschlagen", str(e))
            return
        self.dirty = False
        self.status.config(
            text=f"Gespeichert: {datetime.now():%d.%m.%Y %H:%M}")

    def _on_close(self):
        self._uebernehmen()
        if self.dirty and not messagebox.askyesno(
            "Ungespeicherte Änderungen",
            "Es gibt ungespeicherte Änderungen. Wirklich schließen?"
        ):
            return
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
