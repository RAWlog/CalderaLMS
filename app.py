import os
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'caldera-secret-key-123' # Для сессий
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB макс

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Создаем папку для загрузок, если нет
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    full_name = db.Column(db.String(150))
    role = db.Column(db.String(50)) # 'mentor' или 'intern'
    
    # Если это стажер, он привязан к наставнику
    mentor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    interns = db.relationship('User', remote_side=[id], backref='mentor')

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    mentor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    chapters = db.relationship('Chapter', backref='course', lazy=True, cascade="all, delete-orphan")

class Chapter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'))
    files = db.relationship('File', backref='chapter', lazy=True, cascade="all, delete-orphan")

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(300))
    filepath = db.Column(db.String(300))
    file_size = db.Column(db.String(50))
    chapter_id = db.Column(db.Integer, db.ForeignKey('chapter.id'))
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Кто загрузил (наставник или стажер)
    
    uploader = db.relationship('User', backref='files')

# --- ЛОГИКА ---

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 1. Главная (Выбор роли)
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# 2. Логин (Единый для всех, роль определяется в БД)
@app.route('/login/<role_req>', methods=['GET', 'POST'])
def login(role_req):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        # Простая проверка (в реальности используй хеширование!)
        if user and user.password == password:
            if user.role != role_req:
                flash(f'Ошибка! Этот логин принадлежит роли {user.role}, а вы входите как {role_req}', 'danger')
            else:
                login_user(user, remember=True)
                return redirect(url_for('dashboard'))
        else:
            flash('Неверный логин или пароль', 'danger')
            
    return render_template('login.html', role=role_req)

# 3. Дэшборд (Общий, но контент разный)
@app.route('/dashboard')
@login_required
def dashboard():
    # Определяем, чьи курсы показывать
    target_mentor = current_user
    
    if current_user.role == 'intern':
        if not current_user.mentor_id:
            return "У вас нет наставника!"
        target_mentor = User.query.get(current_user.mentor_id)

    # Загружаем курсы целевого наставника
    courses = Course.query.filter_by(mentor_id=target_mentor.id).all()
    
    return render_template('dashboard.html', courses=courses, mentor=target_mentor)

# --- API ДЛЯ ИЗМЕНЕНИЙ (Только Mentor) ---

@app.route('/create_course', methods=['POST'])
@login_required
def create_course():
    if current_user.role == 'mentor':
        title = request.form.get('title')
        new_course = Course(title=title, mentor_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_course/<int:id>')
@login_required
def delete_course(id):
    course = Course.query.get_or_404(id)
    if current_user.role == 'mentor' and course.mentor_id == current_user.id:
        db.session.delete(course)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/add_chapter/<int:course_id>', methods=['POST'])
@login_required
def add_chapter(course_id):
    course = Course.query.get_or_404(course_id)
    if current_user.role == 'mentor' and course.mentor_id == current_user.id:
        new_chap = Chapter(title="Новая тема", description="Описание...", course_id=course.id)
        db.session.add(new_chap)
        db.session.commit()
        # Возвращаем данные новой темы, чтобы JS мог её нарисовать
        return jsonify({
            'status': 'success', 
            'id': new_chap.id, 
            'title': new_chap.title,
            'description': new_chap.description
        })
    return jsonify({'status': 'error'}), 403

@app.route('/update_chapter/<int:chapter_id>', methods=['POST'])
@login_required
def update_chapter(chapter_id):
    chapter = Chapter.query.get_or_404(chapter_id)
    course = Course.query.get(chapter.course_id)
    
    if current_user.role == 'mentor' and course.mentor_id == current_user.id:
        chapter.title = request.form.get('title', chapter.title)
        chapter.description = request.form.get('description', chapter.description)
        db.session.commit()
        return jsonify({'status': 'success', 'title': chapter.title})
    
    return jsonify({'status': 'error'}), 403

@app.route('/upload_file/<int:chapter_id>', methods=['POST'])
@login_required
def upload_file(chapter_id):
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'msg': 'No file'})
    
    files = request.files.getlist('file')
    uploaded_files_data = []

    for file in files:
        if file.filename == '': continue
        
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        
        size = os.path.getsize(path)
        size_str = f"{size / 1024:.0f} Kb" if size < 1024*1024 else f"{size / (1024*1024):.2f} Mb"
        
        new_file = File(
            filename=filename, 
            filepath=filename, 
            file_size=size_str, 
            chapter_id=chapter_id,
            uploader_id=current_user.id
        )
        db.session.add(new_file)
        db.session.commit() # Коммитим сразу, чтобы получить ID

        # Готовим данные для ответа
        uploaded_files_data.append({
            'id': new_file.id,
            'name': new_file.filename,
            'size': new_file.file_size,
            'role': current_user.role,
            'uploaderName': current_user.full_name,
            'downloadUrl': url_for('download', filename=new_file.filename),
            'deleteUrl': url_for('delete_file', file_id=new_file.id),
            'canDelete': True
        })
    
    return jsonify({'status': 'success', 'files': uploaded_files_data})

# --- ЗАГРУЗКА ФАЙЛОВ (Mentor и Intern) ---

# @app.route('/upload_file/<int:chapter_id>', methods=['POST'])
# @login_required
# def upload_file(chapter_id):
#     if 'file' not in request.files:
#         return redirect(url_for('dashboard'))
    
#     files = request.files.getlist('file')
#     for file in files:
#         if file.filename == '':
#             continue
        
#         filename = secure_filename(file.filename)
#         path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
#         file.save(path)
        
#         # Красивый размер
#         size = os.path.getsize(path)
#         size_str = f"{size / 1024:.0f} Kb" if size < 1024*1024 else f"{size / (1024*1024):.2f} Mb"
        
#         new_file = File(
#             filename=filename, 
#             filepath=filename, # храним только имя в папке uploads
#             file_size=size_str, 
#             chapter_id=chapter_id,
#             uploader_id=current_user.id
#         )
#         db.session.add(new_file)
    
#     db.session.commit()
#     return redirect(url_for('dashboard'))

@app.route('/download/<path:filename>')
@login_required
def download(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)

@app.route('/delete_file/<int:file_id>')
@login_required
def delete_file(file_id):
    file_obj = File.query.get_or_404(file_id)
    # Наставник удаляет все, Стажер только свои
    if current_user.role == 'mentor' or (current_user.role == 'intern' and file_obj.uploader_id == current_user.id):
        # Удаление физически (опционально)
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], file_obj.filepath))
        except:
            pass
        db.session.delete(file_obj)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- СКРИПТ ДЛЯ СОЗДАНИЯ БД И ТЕСТОВЫХ ДАННЫХ ---
@app.route('/setup')
def setup():
    with app.app_context():
        db.create_all()
        # Создаем Наставника
        if not User.query.filter_by(username='smentor').first():
            m = User(username='mentor', password='123', role='mentor', full_name='Александр Петров')
            db.session.add(m)
            db.session.commit()

            l = User(username='l', password='123', role='mentor', full_name='Сергей Семенов')
            db.session.add(l)
            db.session.commit()

            j = User(username='j', password='123', role='mentor', full_name='Лёва Батрудинов')
            db.session.add(j)
            db.session.commit()
            
            # Создаем Стажера, привязанного к наставнику
            i = User(username='intern', password='123', role='intern', full_name='Иван Иванов', mentor_id=m.id)
            db.session.add(i)
            db.session.commit()

            i2 = User(username='goga', password='123', role='intern', full_name='Егор Шапутинский', mentor_id=1)
            db.session.add(i2)
            db.session.commit()
            
            # # Создаем Курс
            # c = Course(title='Git Basic', mentor_id=m.id)
            # db.session.add(c)
            # db.session.commit()
            
            # # Создаем главу
            # ch = Chapter(title='Вступление', description='Установка и настройка', course_id=c.id)
            # db.session.add(ch)
            # db.session.commit()

    return "База данных создана! Логин: mentor/123 или intern/123"

if __name__ == '__main__':
    app.run(debug=True)