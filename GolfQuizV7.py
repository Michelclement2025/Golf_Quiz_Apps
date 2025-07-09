import os
import re
import uuid
import random
import pandas as pd

from flask import Flask, render_template, request, redirect, url_for, session
from flask_session import Session
from markupsafe import Markup

# ------------------------
# Initialisation de Flask
# ------------------------

app = Flask(__name__)

app.config['SECRET_KEY'] = 'une_cle_secrete_a_modifier'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_FILE_DIR'] = os.path.join(app.root_path, '.flask_session')

Session(app)

# ------------------------
# Chargement du fichier Excel
# ------------------------

EXCEL_FILE = "BDEssaiPrograme042025VFQCMRA.xlsx"

try:
# Lecture du fichier en forçant toutes les colonnes en chaînes
    DF = pd.read_excel(EXCEL_FILE)

# Nettoyage de la colonne 'réponse' : conversion des booléens en VRAI/FAUX
    if 'réponse' in DF.columns:
        DF['réponse'] = DF['réponse'].apply(
            lambda x: 'VRAI' if str(x).strip().upper() in ['TRUE', '1'] 
            else 'FAUX' if str(x).strip().upper() in ['FALSE', '0']
            else str(x).strip()
        )
# Nettoyage : suppression des ".0" à la fin des valeurs dans "Règle"
    if 'Règle' in DF.columns:
        DF['Règle'] = DF['Règle'].str.replace(r'\.0$', '', regex=True).str.strip()

# Pareil pour "Sous-règle" si besoin
    if 'Sous-règle' in DF.columns:
        DF['Sous-règle'] = DF['Sous-règle'].str.replace(r'\.0$', '', regex=True).str.strip()

# Idem pour les réponses (important pour RA ou V/F mal encodées)
    if 'réponse' in DF.columns:
        DF['réponse'] = DF['réponse'].str.strip()

except Exception as e:
    print(f"Erreur au chargement de {EXCEL_FILE} :", e)
    DF = pd.DataFrame()

print("Valeurs uniques dans la colonne 'réponse' :", DF['réponse'].unique())
# ------------------------
# Fonction utile accessible dans les templates
# ------------------------

def get_answer(page, input_name):
    exam_results = session.get('exam_results', {})
    return exam_results.get(str(page), {}).get(input_name, "")

app.jinja_env.globals.update(get_answer=get_answer)

# ------------------------
# Formatage des questions
# ------------------------

def formatter_question(qtext, qtype):
    if qtype.strip().upper() == 'QCM':
        qtext = re.sub(r'\s*([ABC])\s*[:.)]\s*', r'\n\1) ', qtext)
        qtext = re.sub(r'\n+', '\n', qtext).strip()
    return qtext

def formatter_question_html(qtext, qtype):
    return formatter_question(qtext, qtype).replace('\n', '<br>')

# ------------------------
# Routes
# ------------------------

@app.route('/')
def index():
    def alphanum_key(val):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', val)]

    types = sorted([str(t) for t in DF['Type'].dropna().unique()], key=alphanum_key)
    regles = sorted(set(r.strip() for r in DF['Règle'].dropna().astype(str)), key=alphanum_key)
    sous_regles = sorted([str(s) for s in DF['Sous-règle'].dropna().unique()], key=alphanum_key) if 'Sous-règle' in DF else []
    interpretations = sorted([str(i) for i in DF['Interprétations'].dropna().unique()], key=alphanum_key)
    examens = sorted([str(e) for e in DF['Examens et concours'].dropna().unique()], key=alphanum_key)

    return render_template('index.html', types=types, regles=regles, sous_regles=sous_regles, interpretations=interpretations, examens=examens)

# Dictionnaire temporaire côté serveur
EXAM_SESSIONS = {}

@app.route('/start_test', methods=['POST'])
def start_test():
    mode = request.form.get('mode')
    print(f"MODE REÇU : {mode}")
    session['mode'] = mode

    if mode == 'test_simple':
        nb_q = int(request.form.get('nb_questions', 10))
        filtres = {
            'Type': request.form.getlist('Type'),
            'Règle': request.form.getlist('Règle'),
            'Sous-règle': request.form.getlist('Sous-règle'),
            'Interprétations': request.form.getlist('Interprétations')
        }

        df = DF.copy()
        for k, v in filtres.items():
            if v and v[0] != "":
                df = df[df[k].astype(str).isin([str(x) for x in v])]

        questions = df.sample(min(nb_q, len(df))).reset_index(drop=True)
        questions_list = questions.to_dict(orient="records")

        for q in questions_list:
            q['formatter_question'] = formatter_question_html(q['Question'], q['Type'])

        if not questions_list:
            return render_template('test.html', questions=[], chrono=False, nb_questions=0, message="Aucune question.")

        session['questions'] = questions_list
        return render_template('test.html', questions=questions_list, chrono=True, nb_questions=len(questions_list))

    elif mode == 'examen_blanc':
        selected_exams = request.form.getlist('Examens_et_concours')
        if not selected_exams:
            return "Erreur : aucun examen sélectionné", 400

        df = DF[DF['Examens et concours'].isin(selected_exams)].copy()
        if df.empty:
            return "Aucune question disponible pour l'examen sélectionné.", 400

        exam_ques = {}

        for key, limit in [('RA', 15), ('V/F', 15), ('QCM', 10)]:
            subset = df[df['Type'] == key]
            if not subset.empty:
                questions = subset.sample(min(limit, len(subset))).to_dict(orient='records')
                for q in questions:
                    q['formatter_question'] = formatter_question_html(q['Question'], q['Type'])
                exam_ques[key] = questions

        subset_go = df[df['Auteurs'] == 'GO']
        if not subset_go.empty:
            go_questions = subset_go.sample(min(10, len(subset_go))).to_dict(orient='records')
            for q in go_questions:
                q['formatter_question'] = formatter_question_html(q['Question'], q['Type'])
            exam_ques['GO'] = go_questions

        if not exam_ques:
            return "Aucune question pour les types requis.", 400

        session_id = str(uuid.uuid4())
        EXAM_SESSIONS[session_id] = exam_ques
        session['exam_session_id'] = session_id
        session['exam_results'] = {}

        return redirect(url_for('examen_page', page=1))

    return "Erreur : mode de test non reconnu", 400

@app.route('/examen/<int:page>', methods=['GET', 'POST'])
def examen_page(page):
    PAGES = [
        ('Règles applicables', 'RA', 10),
        ('Vrai/Faux', 'V/F', 15),
        ('Questions choix multiples', 'QCM', 15),
        ('Organisation de compétitions', 'GO', 15)
    ]

    session_id = session.get('exam_session_id')
    if not session_id or session_id not in EXAM_SESSIONS:
        return "Session expirée. Veuillez recommencer.", 400

    exam_data = EXAM_SESSIONS[session_id]

    total_pages = len(PAGES)
    if page > total_pages:
        return redirect(url_for('results_prelim'))

    titre, cle, minutes = PAGES[page - 1]

    if request.method == 'POST':
        reponses = dict(request.form)
        nav_action = request.form.get('nav_action', 'next')
        session['exam_results'][str(page)] = reponses

        next_page = page - 1 if nav_action == 'prev' and page > 1 else page + 1
        return redirect(url_for('examen_page', page=next_page))

    questions = exam_data.get(cle, []) if cle != 'GO' else exam_data.get('GO', [])

    return render_template(
        'examen_page.html',
        questions=questions,
        titre=titre,
        chrono_minutes=minutes,
        page=page,
        total_pages=total_pages,
        message="Aucune question pour cette section." if not questions else None
    )

@app.route('/results_prelim', methods=['POST'])
def results_prelim():
    PAGES = [
        ('Règles applicables', 'RA', 15),
        ('Vrai/Faux', 'V/F', 15),
        ('QCM', 'QCM', 10),
        ('Organisation Compétitions', 'GO', 15)
    ]

    session_id = session.get('exam_session_id')
    exam_results = session.get('exam_results', {})
    if not session_id or session_id not in EXAM_SESSIONS:
        return "Session expirée.", 400

    exam_data = EXAM_SESSIONS[session_id]

    stats = []
    total_correct = 0
    total_questions = 0

    for i, (label, key, max_q) in enumerate(PAGES, 1):
        page_str = str(i)
        user_answers = exam_results.get(page_str, {})
        questions = exam_data.get(key, [])
        correct = 0
        for idx, q in enumerate(questions):
            input_name = f'q{idx+1}'
            expected = str(q.get('réponse', '')).strip().upper()
            given = user_answers.get(input_name, '').strip().upper()
            if given == expected:
                correct += 1
        total = len(questions)
        percent = round((correct / total) * 100) if total else 0
        stats.append({'label': label, 'correct': correct, 'total': total, 'percent': percent})
        total_correct += correct
        total_questions += total

    avg_percent = round((total_correct / total_questions) * 100) if total_questions else 0
    image_file = 'Arbitre_victorieux.png' if avg_percent >= 70 else 'Arbitre hesitant.png'

    return render_template('results_prelim.html', stats=stats, avg_percent=avg_percent, image_file=image_file)

@app.route('/results', methods=['POST'])
def results():
    questions = session.get('questions', [])
    if not questions:
        return "Erreur : aucune question trouvée dans la session.", 400

    answers = []
    correct_answers = []
    score = 0

    for i, question in enumerate(questions):
        input_name = f'reponse_{i + 1}'  # <-- CHANGEMENT ICI
        user_answer = request.form.get(input_name, '').strip()
        correct_answer = str(question.get('réponse', '')).strip()

        if not user_answer:
            user_answer = "Non répondu"

        answers.append(user_answer)
        correct_answers.append(correct_answer)

        if user_answer.upper() == correct_answer.upper() and user_answer != "Non répondu":
            score += 1

        question['formatter_question'] = formatter_question_html(question['Question'], question.get('Type', ''))

    return render_template(
        'results.html',
        questions=questions,
        answers=answers,
        correct_answers=correct_answers,
        score=score
    )

@app.route('/results_exam')
def results_exam():
    session_id = session.get('exam_session_id')
    exam_results = session.get('exam_results', {})
    if not session_id or session_id not in EXAM_SESSIONS:
        return "Session expirée ou invalide", 400

    exam_data = EXAM_SESSIONS[session_id]

    all_questions = []
    all_answers = []
    correct_answers = []
    score = 0

    # Recompiler toutes les questions et les réponses
    for page_idx, (label, key, _) in enumerate([
        ('Règles applicables', 'RA', 15),
        ('Vrai/Faux', 'V/F', 15),
        ('QCM', 'QCM', 10),
        ('Organisation Compétitions', 'GO', 15)
    ], 1):
        page_str = str(page_idx)
        user_answers = exam_results.get(page_str, {})
        questions = exam_data.get(key, [])

        for idx, q in enumerate(questions):
            q['formatter_question'] = formatter_question_html(q['Question'], q.get('Type', ''))
            input_name = f'q{idx+1}'
            user_answer = user_answers.get(input_name, "Non répondu").strip()
            correct = str(q.get('réponse', '')).strip()

            all_questions.append(q)
            all_answers.append(user_answer)
            correct_answers.append(correct)

            if user_answer.upper() == correct.upper():
                score += 1

    return render_template('results.html',
                           questions=all_questions,
                           answers=all_answers,
                           correct_answers=correct_answers,
                           score=score)



# ------------------------
# Lancement de l'application
# ------------------------

if __name__ == "__main__":
    import threading
    import webbrowser
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print(">>> Serveur en cours d'exécution sur http://127.0.0.1:5000")
    app.run(debug=True)

