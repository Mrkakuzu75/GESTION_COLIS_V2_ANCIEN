#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║   ColisExpress — Importation base de données existante   ║
╚══════════════════════════════════════════════════════════╝

USAGE :
  1. Placez votre ancienne base dans le même dossier que ce script
  2. Lancez : python importer_base.py votre_ancienne_base.db
  3. Si votre ancienne base s'appelle déjà "colis.db", lancez :
       python importer_base.py colis_ancien.db

Le script :
  - Détecte les tables disponibles dans l'ancienne base
  - Importe les colis, clients et historiques sans doublon
  - Migre l'ancienne colonne photo_colis vers photos_colis (multi-photos)
  - Affiche un rapport complet à la fin
"""

import sqlite3
import sys
import os
from datetime import datetime


NOUVELLE_DB = 'colis.db'


def get_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_columns(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def table_exists(conn, table):
    r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return r is not None


def init_nouvelle_db(conn):
    """Crée les tables si elles n'existent pas dans la nouvelle DB."""
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS colis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_suivi TEXT,
        type_flux TEXT NOT NULL,
        deposant_nom TEXT, deposant_telephone TEXT,
        livreur_nom TEXT, livreur_telephone TEXT,
        destinataire_nom TEXT, destinataire_telephone TEXT,
        recuperateur_nom TEXT, recuperateur_telephone TEXT,
        type_colis TEXT, type_colis_autre TEXT,
        nombre_colis INTEGER DEFAULT 1,
        poids REAL, poids_arrondi REAL,
        prix_final REAL, montant_paye REAL,
        moyen_payement TEXT, payement_chez TEXT,
        est_livreur INTEGER DEFAULT 1,
        est_negocie INTEGER DEFAULT 0,
        photo_colis TEXT,
        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_validation TIMESTAMP, date_expedition TIMESTAMP,
        date_reception TIMESTAMP, date_recuperation TIMESTAMP,
        statut TEXT DEFAULT "En attente", notes TEXT
    );
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL, telephone TEXT UNIQUE NOT NULL,
        adresse TEXT, observations TEXT,
        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_derniere_activite TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        colis_id INTEGER, action TEXT NOT NULL,
        ancienne_valeur TEXT, nouvelle_valeur TEXT,
        utilisateur TEXT DEFAULT "admin",
        date_action TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS photos_colis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        colis_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        date_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (colis_id) REFERENCES colis(id) ON DELETE CASCADE
    );
    ''')
    conn.commit()


def importer_colis(ancienne, nouvelle):
    if not table_exists(ancienne, 'colis'):
        print("  ⚠️  Table 'colis' introuvable dans l'ancienne base.")
        return 0, 0

    anciens_cols = get_columns(ancienne, 'colis')
    nouveaux_cols = get_columns(nouvelle, 'colis')
    cols_communes = [c for c in anciens_cols if c in nouveaux_cols and c != 'id']

    anciens = ancienne.execute("SELECT * FROM colis").fetchall()
    inseres = 0
    ignores = 0
    id_mapping = {}  # ancien_id -> nouveau_id

    for row in anciens:
        row_dict = dict(row)
        ancien_id = row_dict['id']

        # Vérifier doublon par numero_suivi si disponible
        suivi = row_dict.get('numero_suivi')
        if suivi:
            existing = nouvelle.execute(
                "SELECT id FROM colis WHERE numero_suivi=?", (suivi,)
            ).fetchone()
            if existing:
                id_mapping[ancien_id] = existing['id']
                ignores += 1
                continue

        vals = {c: row_dict.get(c) for c in cols_communes}
        placeholders = ', '.join(['?' for _ in cols_communes])
        col_names = ', '.join(cols_communes)
        cur = nouvelle.execute(
            f"INSERT INTO colis ({col_names}) VALUES ({placeholders})",
            [vals[c] for c in cols_communes]
        )
        nouveau_id = cur.lastrowid
        id_mapping[ancien_id] = nouveau_id
        inseres += 1

        # Migrer photo_colis → photos_colis
        photo = row_dict.get('photo_colis')
        if photo:
            existing_photo = nouvelle.execute(
                "SELECT id FROM photos_colis WHERE colis_id=? AND filename=?",
                (nouveau_id, photo)
            ).fetchone()
            if not existing_photo:
                nouvelle.execute(
                    "INSERT INTO photos_colis (colis_id, filename) VALUES (?,?)",
                    (nouveau_id, photo)
                )

    nouvelle.commit()
    return inseres, ignores, id_mapping


def importer_clients(ancienne, nouvelle):
    if not table_exists(ancienne, 'clients'):
        print("  ⚠️  Table 'clients' introuvable dans l'ancienne base.")
        return 0, 0

    anciens_cols = get_columns(ancienne, 'clients')
    nouveaux_cols = get_columns(nouvelle, 'clients')
    cols_communes = [c for c in anciens_cols if c in nouveaux_cols and c != 'id']

    anciens = ancienne.execute("SELECT * FROM clients").fetchall()
    inseres = ignores = 0

    for row in anciens:
        row_dict = dict(row)
        tel = row_dict.get('telephone', '')
        existing = nouvelle.execute("SELECT id FROM clients WHERE telephone=?", (tel,)).fetchone()
        if existing:
            ignores += 1
            continue

        vals = {c: row_dict.get(c) for c in cols_communes}
        placeholders = ', '.join(['?' for _ in cols_communes])
        col_names = ', '.join(cols_communes)
        try:
            nouvelle.execute(
                f"INSERT INTO clients ({col_names}) VALUES ({placeholders})",
                [vals[c] for c in cols_communes]
            )
            inseres += 1
        except Exception as e:
            print(f"  ⚠️  Client ignoré ({row_dict.get('nom')}) : {e}")
            ignores += 1

    nouvelle.commit()
    return inseres, ignores


def importer_historique(ancienne, nouvelle, id_mapping):
    if not table_exists(ancienne, 'historique'):
        print("  ⚠️  Table 'historique' introuvable dans l'ancienne base.")
        return 0

    anciens_cols = get_columns(ancienne, 'historique')
    nouveaux_cols = get_columns(nouvelle, 'historique')
    cols_communes = [c for c in anciens_cols if c in nouveaux_cols and c not in ('id', 'colis_id')]

    anciens = ancienne.execute("SELECT * FROM historique").fetchall()
    inseres = 0

    for row in anciens:
        row_dict = dict(row)
        ancien_colis_id = row_dict.get('colis_id')
        nouveau_colis_id = id_mapping.get(ancien_colis_id, ancien_colis_id)

        vals = {c: row_dict.get(c) for c in cols_communes}
        col_names = 'colis_id, ' + ', '.join(cols_communes)
        placeholders = ', '.join(['?' for _ in range(len(cols_communes) + 1)])
        nouvelle.execute(
            f"INSERT INTO historique ({col_names}) VALUES ({placeholders})",
            [nouveau_colis_id] + [vals[c] for c in cols_communes]
        )
        inseres += 1

    nouvelle.commit()
    return inseres


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nERREUR : Indiquez le chemin de votre ancienne base.")
        print("  Exemple : python importer_base.py mon_ancienne_base.db")
        sys.exit(1)

    ancienne_path = sys.argv[1]

    if not os.path.exists(ancienne_path):
        print(f"❌ Fichier introuvable : {ancienne_path}")
        sys.exit(1)

    if os.path.abspath(ancienne_path) == os.path.abspath(NOUVELLE_DB):
        print("❌ L'ancienne et la nouvelle base sont le même fichier !")
        print("   Renommez votre ancienne base (ex: colis_ancien.db) avant de lancer ce script.")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║   ColisExpress — Import de base de données               ║
╠══════════════════════════════════════════════════════════╣
║  Source  : {ancienne_path:<43}║
║  Cible   : {NOUVELLE_DB:<43}║
╚══════════════════════════════════════════════════════════╝
""")

    # Ouvrir les deux bases
    ancienne = get_conn(ancienne_path)
    nouvelle = get_conn(NOUVELLE_DB)

    print("🔧 Initialisation de la nouvelle base…")
    init_nouvelle_db(nouvelle)

    # Afficher les tables disponibles dans l'ancienne base
    tables_anciennes = ancienne.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    print(f"📋 Tables détectées dans l'ancienne base : {[t[0] for t in tables_anciennes]}")
    print()

    # Importer
    print("📦 Import des colis…")
    result = importer_colis(ancienne, nouvelle)
    inseres_c, ignores_c, id_mapping = result
    print(f"   ✅ {inseres_c} colis importés, {ignores_c} déjà existants ignorés")

    print("👥 Import des clients…")
    inseres_cl, ignores_cl = importer_clients(ancienne, nouvelle)
    print(f"   ✅ {inseres_cl} clients importés, {ignores_cl} déjà existants ignorés")

    print("📜 Import de l'historique…")
    inseres_h = importer_historique(ancienne, nouvelle, id_mapping)
    print(f"   ✅ {inseres_h} entrées d'historique importées")

    # Stats finales
    total_colis = nouvelle.execute("SELECT COUNT(*) FROM colis").fetchone()[0]
    total_clients = nouvelle.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    total_photos = nouvelle.execute("SELECT COUNT(*) FROM photos_colis").fetchone()[0]

    print(f"""
╔══════════════════════════════════════════════════════════╗
║   RAPPORT FINAL                                          ║
╠══════════════════════════════════════════════════════════╣
║  Colis dans la nouvelle base  : {total_colis:<25}║
║  Clients dans la nouvelle base: {total_clients:<25}║
║  Photos migrées               : {total_photos:<25}║
╚══════════════════════════════════════════════════════════╝

✅ Import terminé avec succès !
   Lancez maintenant : python app.py
""")

    ancienne.close()
    nouvelle.close()


if __name__ == '__main__':
    main()
