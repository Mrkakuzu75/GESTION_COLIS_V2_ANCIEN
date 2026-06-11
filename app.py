import os
import math
import sqlite3
import json
import csv
import io
import base64
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_file, make_response)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'colispro_secret_key_2024'

DB_PATH = 'colis.db'
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
TAUX_CONVERSION = 655.96
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ─── DATABASE ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS colis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_suivi TEXT,
        type_flux TEXT NOT NULL,
        deposant_nom TEXT,
        deposant_telephone TEXT,
        livreur_nom TEXT,
        livreur_telephone TEXT,
        destinataire_nom TEXT,
        destinataire_telephone TEXT,
        recuperateur_nom TEXT,
        recuperateur_telephone TEXT,
        type_colis TEXT,
        type_colis_autre TEXT,
        nombre_colis INTEGER DEFAULT 1,
        poids REAL,
        poids_arrondi REAL,
        prix_final REAL,
        montant_paye REAL,
        moyen_payement TEXT,
        payement_chez TEXT,
        est_livreur INTEGER DEFAULT 1,
        est_negocie INTEGER DEFAULT 0,
        photo_colis TEXT,
        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_validation TIMESTAMP,
        date_expedition TIMESTAMP,
        date_reception TIMESTAMP,
        date_recuperation TIMESTAMP,
        statut TEXT DEFAULT "En attente",
        notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        telephone TEXT UNIQUE NOT NULL,
        adresse TEXT,
        observations TEXT,
        date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_derniere_activite TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        colis_id INTEGER,
        action TEXT NOT NULL,
        ancienne_valeur TEXT,
        nouvelle_valeur TEXT,
        utilisateur TEXT DEFAULT "admin",
        date_action TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # Table photos (multi-photos par colis)
    c.execute('''CREATE TABLE IF NOT EXISTS photos_colis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        colis_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        date_upload TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (colis_id) REFERENCES colis(id) ON DELETE CASCADE
    )''')
    # Migration : si ancienne colonne photo_colis existe, on migre vers photos_colis
    try:
        cols = [row[1] for row in c.execute("PRAGMA table_info(colis)").fetchall()]
        if 'photo_colis' in cols:
            rows = conn.execute("SELECT id, photo_colis FROM colis WHERE photo_colis IS NOT NULL AND photo_colis != ''").fetchall()
            for row in rows:
                existing = conn.execute("SELECT id FROM photos_colis WHERE colis_id=? AND filename=?", (row[0], row[1])).fetchone()
                if not existing:
                    conn.execute("INSERT INTO photos_colis (colis_id, filename) VALUES (?,?)", (row[0], row[1]))
    except:
        pass
    conn.commit()
    conn.close()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def arrondi_superieur(poids):
    if poids <= 0:
        return 0
    return math.ceil(poids)

def generer_numero_suivi():
    today = datetime.now().strftime("%Y%m%d")
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM colis WHERE numero_suivi LIKE ?", (f"CE-{today}-%",)
    ).fetchone()[0]
    conn.close()
    return f"CE-{today}-{str(count + 1).zfill(4)}"

def nettoyer_telephone(tel):
    if not tel:
        return ''
    return ''.join(c for c in tel if c.isdigit())

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ajouter_historique(colis_id, action, ancienne=None, nouvelle=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO historique (colis_id, action, ancienne_valeur, nouvelle_valeur) VALUES (?,?,?,?)",
        (colis_id, action, ancienne, nouvelle)
    )
    conn.commit()
    conn.close()

def get_photos(colis_id):
    conn = get_db()
    photos = conn.execute(
        "SELECT * FROM photos_colis WHERE colis_id=? ORDER BY date_upload", (colis_id,)
    ).fetchall()
    conn.close()
    return photos

def sauvegarder_photos(colis_id, numero_suivi, files):
    """Sauvegarde plusieurs fichiers photo pour un colis."""
    conn = get_db()
    saved = []
    for f in files:
        if f and f.filename and allowed_file(f.filename):
            ext = f.filename.rsplit('.', 1)[1].lower()
            fname = f"{numero_suivi}_{datetime.now().strftime('%H%M%S%f')}.{ext}"
            f.save(os.path.join(UPLOAD_FOLDER, fname))
            conn.execute("INSERT INTO photos_colis (colis_id, filename) VALUES (?,?)", (colis_id, fname))
            saved.append(fname)
    conn.commit()
    conn.close()
    return saved

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def format_date(dt_str):
    if not dt_str:
        return '-'
    try:
        dt = datetime.fromisoformat(str(dt_str))
        return dt.strftime('%d/%m/%Y %H:%M')
    except:
        return str(dt_str)

app.jinja_env.globals['format_date'] = format_date
app.jinja_env.globals['now'] = datetime.now

# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == 'admin123':
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = 'Mot de passe incorrect.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

# ─── INDEX ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    mois = request.args.get('mois', datetime.now().strftime('%Y-%m'))
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM colis").fetchone()[0]
    ca_mois = conn.execute(
        "SELECT COALESCE(SUM(prix_final),0) FROM colis WHERE strftime('%Y-%m', date_creation)=? AND type_flux='envoi_france'",
        (mois,)
    ).fetchone()[0]
    en_attente = conn.execute("SELECT COUNT(*) FROM colis WHERE statut='En attente'").fetchone()[0]
    envois_mois = conn.execute(
        "SELECT COUNT(*) FROM colis WHERE strftime('%Y-%m', date_creation)=? AND type_flux='envoi_france'",
        (mois,)
    ).fetchone()[0]
    mois_dispo = conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', date_creation) as m FROM colis ORDER BY m DESC"
    ).fetchall()
    conn.close()
    return render_template('index.html',
        total=total, ca_mois=ca_mois, en_attente=en_attente,
        envois_mois=envois_mois, mois=mois,
        mois_dispo=[r['m'] for r in mois_dispo],
        taux=TAUX_CONVERSION)

# ─── ADD COLIS ────────────────────────────────────────────────────────────────

@app.route('/add', methods=['GET', 'POST'])
def add_colis():
    if request.method == 'POST':
        data = request.form
        type_flux = data.get('type_flux', 'envoi_france')
        # Mode livreur = par défaut coché (1), décoché = déposant (0)
        est_livreur = 1 if data.get('est_livreur') else 0
        est_negocie = 1 if data.get('est_negocie') else 0

        poids = float(data.get('poids') or 0)
        poids_arrondi = arrondi_superieur(poids)

        if est_negocie:
            prix_final = float(data.get('prix_negocie') or 0)
        else:
            prix_final = poids_arrondi * 10

        numero_suivi = generer_numero_suivi()

        conn = get_db()
        cur = conn.execute('''INSERT INTO colis
            (numero_suivi,type_flux,deposant_nom,deposant_telephone,livreur_nom,livreur_telephone,
             destinataire_nom,destinataire_telephone,type_colis,type_colis_autre,nombre_colis,
             poids,poids_arrondi,prix_final,moyen_payement,payement_chez,
             est_livreur,est_negocie,notes,statut)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"En attente")''',
            (numero_suivi, type_flux,
             data.get('deposant_nom'), nettoyer_telephone(data.get('deposant_telephone', '')),
             data.get('livreur_nom'), nettoyer_telephone(data.get('livreur_telephone', '')),
             data.get('destinataire_nom'), nettoyer_telephone(data.get('destinataire_telephone', '')),
             data.get('type_colis'), data.get('type_colis_autre'),
             int(data.get('nombre_colis') or 1),
             poids, poids_arrondi, prix_final,
             data.get('moyen_payement'), data.get('payement_chez'),
             est_livreur, est_negocie,
             data.get('notes')))
        colis_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Multi-photos
        photos = request.files.getlist('photos')
        if photos:
            sauvegarder_photos(colis_id, numero_suivi, photos)

        ajouter_historique(colis_id, 'Création', None, 'Statut: En attente')
        return redirect(url_for('list_colis'))

    conn = get_db()
    clients = conn.execute("SELECT * FROM clients ORDER BY nom").fetchall()
    conn.close()
    return render_template('add_colis.html', clients=clients)

# ─── LIST COLIS ───────────────────────────────────────────────────────────────

@app.route('/list')
@login_required
def list_colis():
    filtre = request.args.get('filtre', 'tous')
    conn = get_db()
    q = "SELECT * FROM colis"
    if filtre == 'envois':
        q += " WHERE type_flux='envoi_france'"
    elif filtre == 'receptions':
        q += " WHERE type_flux='reception_france'"
    elif filtre in ('En attente', 'Parti', 'Récupéré'):
        q += f" WHERE statut='{filtre}'"
    q += " ORDER BY date_creation DESC"
    colis = conn.execute(q).fetchall()

    # Compter les photos par colis
    photos_count = {}
    rows = conn.execute("SELECT colis_id, COUNT(*) as nb FROM photos_colis GROUP BY colis_id").fetchall()
    for r in rows:
        photos_count[r['colis_id']] = r['nb']
    conn.close()
    return render_template('list_colis.html', colis=colis, filtre=filtre, photos_count=photos_count)

# ─── PHOTOS D'UN COLIS ───────────────────────────────────────────────────────

@app.route('/photos/<int:colis_id>')
@login_required
def photos_colis(colis_id):
    conn = get_db()
    colis = conn.execute("SELECT * FROM colis WHERE id=?", (colis_id,)).fetchone()
    photos = conn.execute(
        "SELECT * FROM photos_colis WHERE colis_id=? ORDER BY date_upload", (colis_id,)
    ).fetchall()
    conn.close()
    if not colis:
        return redirect(url_for('list_colis'))
    return render_template('photos_colis.html', colis=colis, photos=photos)

@app.route('/photos/<int:colis_id>/upload', methods=['POST'])
@login_required
def upload_photo(colis_id):
    conn = get_db()
    colis = conn.execute("SELECT numero_suivi FROM colis WHERE id=?", (colis_id,)).fetchone()
    conn.close()
    if not colis:
        return jsonify({'error': 'Colis introuvable'}), 404
    photos = request.files.getlist('photos')
    saved = sauvegarder_photos(colis_id, colis['numero_suivi'] or str(colis_id), photos)
    return jsonify({'success': True, 'saved': saved, 'count': len(saved)})

@app.route('/photos/delete/<int:photo_id>', methods=['DELETE'])
@login_required
def delete_photo(photo_id):
    conn = get_db()
    photo = conn.execute("SELECT * FROM photos_colis WHERE id=?", (photo_id,)).fetchone()
    if photo:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, photo['filename']))
        except:
            pass
        conn.execute("DELETE FROM photos_colis WHERE id=?", (photo_id,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

# ─── SUIVI ────────────────────────────────────────────────────────────────────

@app.route('/suivi')
def suivi():
    q = request.args.get('q', '').strip()
    resultats = []
    if q:
        conn = get_db()
        terme = nettoyer_telephone(q)
        resultats = conn.execute('''SELECT * FROM colis WHERE
            REPLACE(REPLACE(REPLACE(destinataire_telephone," ",""),"-",""),"+","") LIKE ?
            OR LOWER(destinataire_nom) LIKE ?
            OR REPLACE(REPLACE(REPLACE(deposant_telephone," ",""),"-",""),"+","") LIKE ?
            OR LOWER(deposant_nom) LIKE ?
            ORDER BY date_creation DESC''',
            (f'%{terme}%', f'%{q.lower()}%', f'%{terme}%', f'%{q.lower()}%')
        ).fetchall()
        conn.close()
    return render_template('suivi.html', resultats=resultats, query=q)

# ─── RÉCUPÉRATION ─────────────────────────────────────────────────────────────

@app.route('/recuperation')
@login_required
def recuperation():
    q = request.args.get('q', '').strip()
    colis = []
    if q:
        terme = nettoyer_telephone(q)
        conn = get_db()
        colis = conn.execute('''SELECT * FROM colis WHERE statut="En attente"
            AND type_flux="reception_france" AND (
            REPLACE(REPLACE(REPLACE(destinataire_telephone," ",""),"-",""),"+","") LIKE ?
            OR LOWER(destinataire_nom) LIKE ?)
            ORDER BY date_creation DESC''',
            (f'%{terme}%', f'%{q.lower()}%')
        ).fetchall()
        conn.close()
    return render_template('recuperation.html', colis=colis, query=q)

# ─── STATISTIQUES ─────────────────────────────────────────────────────────────

@app.route('/statistiques')
@login_required
def statistiques():
    mois = request.args.get('mois', datetime.now().strftime('%Y-%m'))
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    ca_jour = conn.execute(
        "SELECT COALESCE(SUM(prix_final),0) FROM colis WHERE DATE(date_creation)=? AND type_flux='envoi_france'",
        (today,)
    ).fetchone()[0]
    ca_mois = conn.execute(
        "SELECT COALESCE(SUM(prix_final),0) FROM colis WHERE strftime('%Y-%m',date_creation)=? AND type_flux='envoi_france'",
        (mois,)
    ).fetchone()[0]
    montant_recupere = conn.execute(
        "SELECT COALESCE(SUM(montant_paye),0) FROM colis WHERE strftime('%Y-%m',date_recuperation)=? AND statut='Récupéré'",
        (mois,)
    ).fetchone()[0]
    colis_attente = conn.execute("SELECT COUNT(*) FROM colis WHERE statut='En attente'").fetchone()[0]
    colis_recuperes = conn.execute(
        "SELECT COUNT(*) FROM colis WHERE statut='Récupéré' AND strftime('%Y-%m',date_recuperation)=?",
        (mois,)
    ).fetchone()[0]

    limite_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    urgents = conn.execute(
        "SELECT * FROM colis WHERE statut='En attente' AND type_flux='reception_france' AND date_creation <= ? ORDER BY date_creation",
        (limite_date,)
    ).fetchall()

    mois_dispo = conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', date_creation) as m FROM colis ORDER BY m DESC"
    ).fetchall()
    conn.close()

    return render_template('statistiques.html',
        ca_jour=ca_jour, ca_mois=ca_mois,
        montant_recupere=montant_recupere,
        benefice=ca_mois - montant_recupere,
        colis_attente=colis_attente,
        colis_recuperes=colis_recuperes,
        urgents=urgents, mois=mois,
        mois_dispo=[r['m'] for r in mois_dispo],
        taux=TAUX_CONVERSION)

# ─── CLIENTS ──────────────────────────────────────────────────────────────────

@app.route('/clients')
@login_required
def clients():
    q = request.args.get('q', '').strip()
    conn = get_db()
    if q:
        clients_list = conn.execute(
            "SELECT * FROM clients WHERE LOWER(nom) LIKE ? OR telephone LIKE ? ORDER BY nom",
            (f'%{q.lower()}%', f'%{q}%')
        ).fetchall()
    else:
        clients_list = conn.execute("SELECT * FROM clients ORDER BY nom").fetchall()
    conn.close()
    return render_template('clients.html', clients=clients_list, query=q)

# ─── HISTORIQUE ───────────────────────────────────────────────────────────────

@app.route('/historique/<int:colis_id>')
@login_required
def historique(colis_id):
    conn = get_db()
    colis = conn.execute("SELECT * FROM colis WHERE id=?", (colis_id,)).fetchone()
    hist = conn.execute(
        "SELECT * FROM historique WHERE colis_id=? ORDER BY date_action DESC",
        (colis_id,)
    ).fetchall()
    conn.close()
    if not colis:
        return redirect(url_for('list_colis'))
    return render_template('historique.html', colis=colis, historique=hist)

# ─── REÇU A5 ──────────────────────────────────────────────────────────────────

@app.route('/recu-a5/<int:colis_id>')
def recu_a5(colis_id):
    conn = get_db()
    colis = conn.execute("SELECT * FROM colis WHERE id=?", (colis_id,)).fetchone()
    photos = conn.execute(
        "SELECT * FROM photos_colis WHERE colis_id=? ORDER BY date_upload LIMIT 1", (colis_id,)
    ).fetchall()
    conn.close()
    if not colis:
        return "Colis introuvable", 404

    qr_b64 = None
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(colis['numero_suivi'] or str(colis_id))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except:
        pass

    return render_template('recu_a5.html', colis=colis, qr_b64=qr_b64, photos=photos)

# ─── API ──────────────────────────────────────────────────────────────────────

@app.route('/api/recherche')
def api_recherche():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    terme = nettoyer_telephone(q)
    conn = get_db()
    rows = conn.execute('''SELECT * FROM colis WHERE
        REPLACE(REPLACE(REPLACE(destinataire_telephone," ",""),"-",""),"+","") LIKE ?
        OR LOWER(destinataire_nom) LIKE ? LIMIT 20''',
        (f'%{terme}%', f'%{q.lower()}%')
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/colis')
@login_required
def api_colis():
    conn = get_db()
    rows = conn.execute("SELECT * FROM colis ORDER BY date_creation DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/colis/<int:colis_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def api_colis_detail(colis_id):
    conn = get_db()
    if request.method == 'GET':
        row = conn.execute("SELECT * FROM colis WHERE id=?", (colis_id,)).fetchone()
        conn.close()
        return jsonify(dict(row)) if row else ('Not found', 404)

    elif request.method == 'PUT':
        data = request.get_json()
        nouveau_statut = data.get('statut')
        ancien = conn.execute("SELECT statut FROM colis WHERE id=?", (colis_id,)).fetchone()
        if not ancien:
            conn.close()
            return jsonify({'error': 'Not found'}), 404
        ancien_statut = ancien['statut']
        champ_date = ''
        if nouveau_statut == 'Parti':
            champ_date = ', date_expedition=CURRENT_TIMESTAMP'
        elif nouveau_statut == 'Récupéré':
            champ_date = ', date_recuperation=CURRENT_TIMESTAMP'
        conn.execute(f"UPDATE colis SET statut=?{champ_date} WHERE id=?", (nouveau_statut, colis_id))
        conn.commit()
        conn.close()
        ajouter_historique(colis_id, 'Changement statut', ancien_statut, nouveau_statut)
        return jsonify({'success': True})

    elif request.method == 'DELETE':
        row = conn.execute("SELECT numero_suivi FROM colis WHERE id=?", (colis_id,)).fetchone()
        # Supprimer les fichiers photos
        photos = conn.execute("SELECT filename FROM photos_colis WHERE colis_id=?", (colis_id,)).fetchall()
        for p in photos:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, p['filename']))
            except:
                pass
        conn.execute("DELETE FROM photos_colis WHERE colis_id=?", (colis_id,))
        conn.execute("DELETE FROM colis WHERE id=?", (colis_id,))
        conn.commit()
        conn.close()
        if row:
            ajouter_historique(colis_id, 'Suppression', row['numero_suivi'], None)
        return jsonify({'success': True})

@app.route('/api/colis/<int:colis_id>/statut', methods=['POST'])
@login_required
def api_recuperer(colis_id):
    data = request.get_json()
    montant = float(data.get('montant_paye') or 0)
    nom_rec = data.get('recuperateur_nom', '')
    conn = get_db()
    ancien = conn.execute("SELECT statut FROM colis WHERE id=?", (colis_id,)).fetchone()
    conn.execute('''UPDATE colis SET statut="Récupéré",
        montant_paye=?, recuperateur_nom=?, date_recuperation=CURRENT_TIMESTAMP
        WHERE id=?''', (montant, nom_rec, colis_id))
    conn.commit()
    conn.close()
    ajouter_historique(colis_id, 'Récupération', ancien['statut'] if ancien else None,
                       f'Récupéré par {nom_rec} - {montant}€')
    return jsonify({'success': True})

@app.route('/api/clients', methods=['GET', 'POST'])
@login_required
def api_clients():
    conn = get_db()
    if request.method == 'GET':
        rows = conn.execute("SELECT * FROM clients ORDER BY nom").fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    data = request.get_json()
    try:
        conn.execute("INSERT INTO clients (nom,telephone,adresse,observations) VALUES (?,?,?,?)",
            (data['nom'], nettoyer_telephone(data['telephone']),
             data.get('adresse'), data.get('observations')))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/clients/<int:client_id>', methods=['PUT', 'DELETE'])
@login_required
def api_client_detail(client_id):
    conn = get_db()
    if request.method == 'PUT':
        data = request.get_json()
        conn.execute(
            "UPDATE clients SET nom=?,telephone=?,adresse=?,observations=?,date_derniere_activite=CURRENT_TIMESTAMP WHERE id=?",
            (data['nom'], nettoyer_telephone(data['telephone']),
             data.get('adresse'), data.get('observations'), client_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/clients/recherche')
@login_required
def api_clients_recherche():
    q = request.args.get('q', '').strip()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM clients WHERE LOWER(nom) LIKE ? OR telephone LIKE ? LIMIT 10",
        (f'%{q.lower()}%', f'%{q}%')
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/stats')
def api_stats():
    mois = request.args.get('mois', datetime.now().strftime('%Y-%m'))
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM colis").fetchone()[0]
    ca = conn.execute(
        "SELECT COALESCE(SUM(prix_final),0) FROM colis WHERE strftime('%Y-%m',date_creation)=? AND type_flux='envoi_france'",
        (mois,)
    ).fetchone()[0]
    en_attente = conn.execute("SELECT COUNT(*) FROM colis WHERE statut='En attente'").fetchone()[0]
    envois_mois = conn.execute(
        "SELECT COUNT(*) FROM colis WHERE strftime('%Y-%m',date_creation)=? AND type_flux='envoi_france'",
        (mois,)
    ).fetchone()[0]
    conn.close()
    return jsonify({'total': total, 'ca': ca, 'en_attente': en_attente, 'envois_mois': envois_mois})

@app.route('/api/mois-disponibles')
def api_mois():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT strftime('%Y-%m', date_creation) as m FROM colis ORDER BY m DESC"
    ).fetchall()
    conn.close()
    return jsonify([r['m'] for r in rows])

@app.route('/export/excel')
@login_required
def export_excel():
    conn = get_db()
    colis = conn.execute("SELECT * FROM colis ORDER BY date_creation DESC").fetchall()
    conn.close()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['N° Suivi','ID','Type','Expéditeur','Destinataire','Téléphone',
                     'Type Colis','Poids','Prix','Statut','Date création','Date départ',
                     'Date récupération','Notes'])
    for c in colis:
        flux = 'CI→FR' if c['type_flux'] == 'envoi_france' else 'FR→CI'
        expediteur = c['deposant_nom'] or c['livreur_nom'] or '-'
        writer.writerow([
            c['numero_suivi'] or '', c['id'], flux, expediteur,
            c['destinataire_nom'] or '', c['destinataire_telephone'] or '',
            c['type_colis'] or '', c['poids'] or '', c['prix_final'] or '',
            c['statut'], format_date(c['date_creation']),
            format_date(c['date_expedition']), format_date(c['date_recuperation']),
            c['notes'] or ''
        ])
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = (
        f'attachment; filename=colisexpress_{datetime.now().strftime("%Y%m%d")}.csv'
    )
    return response

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs('backups', exist_ok=True)
    init_db()
    app.run(debug=True, port=5000)
