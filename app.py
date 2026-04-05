import os
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from datetime import timedelta
import uuid
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'caldera-secret-key-123' # Для сессий
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30) # Запомнить на месяц

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
    #interns = db.relationship('User', remote_side=[id], backref='mentor')
    mentor = db.relationship('User', remote_side=[id], backref='interns')

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
    is_approved = db.Column(db.Boolean, default=False)
    # ДОБАВЛЯЕМ ЭТУ СТРОКУ:
    upload_date = db.Column(db.DateTime, default=datetime.now)
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

# 2. Логин (обновленная логика)
@app.route('/login/<role_req>', methods=['GET', 'POST'])
def login(role_req):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Получаем значение галочки из формы
        # Если галочка нажата, придет 'on', если нет — None
        remember_me = True if request.form.get('remember') else False
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password:
            if user.role != role_req:
                flash(f'Ошибка! Этот логин принадлежит роли {user.role}', 'danger')
            else:
                # ПЕРЕДАЕМ ПЕРЕМЕННУЮ СЮДА
                login_user(user, remember=remember_me)
                return redirect(url_for('dashboard'))
        else:
            flash('Неверный логин или пароль', 'danger')
            
    return render_template('login.html', role=role_req)

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
    
    # --- СИНХРОНИЗАЦИЯ: Отсеиваем "призраков" ---
    for course in courses:
        for chapter in course.chapters:
            # Создаем пустой список для реально существующих файлов
            chapter.active_files = [] 
            
            for file in chapter.files:
                # Строим полный путь к файлу на жестком диске
                full_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filepath)
                
                # Проверяем, существует ли файл физически
                if os.path.exists(full_path):
                    chapter.active_files.append(file)
                else:
                    # Если файла нет в папке, удаляем "мертвую" запись из базы данных
                    db.session.delete(file)
                    print(f"Удален призрачный файл из БД: {file.filename}") # Для отладки в консоли
                    
    # Сохраняем изменения в базе (если что-то было удалено)
    db.session.commit()

    return render_template('dashboard.html', courses=courses, mentor=target_mentor)

# --- API ДЛЯ ИЗМЕНЕНИЙ (Только Mentor) ---

@app.route('/create_course', methods=['POST'])
@login_required
def create_course():
    if current_user.role == 'mentor':
        title = request.form.get('title')
        
        # Небольшая защита от пустых имен
        if not title or title.strip() == '':
            return jsonify({'status': 'error', 'msg': 'Название не может быть пустым'}), 400
            
        new_course = Course(title=title, mentor_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        
        # Возвращаем JSON с ID и названием нового курса
        return jsonify({
            'status': 'success',
            'id': new_course.id,
            'title': new_course.title
        })
        
    return jsonify({'status': 'error', 'msg': 'Нет прав'}), 403

@app.route('/delete_course/<int:id>', methods=['POST', 'GET']) # Убедись, что методы указаны
@login_required
def delete_course(id):
    course = Course.query.get_or_404(id)
    if course.mentor_id != current_user.id:
        return jsonify({'status': 'error', 'msg': 'Чужое удалять нельзя!'}), 403
    
    db.session.delete(course)
    db.session.commit()
    
    # Вместо redirect возвращаем успех
    return jsonify({'status': 'success'})

@app.route('/delete_chapter/<int:id>', methods=['POST'])
def delete_chapter(id):
    chapter = Chapter.query.get_or_404(id)
    db.session.delete(chapter)
    db.session.commit()
    # Возвращаем JSON, а не редирект!
    return jsonify({'status': 'success', 'id': id})

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
        # 1. Запоминаем оригинальное русское имя
        original_filename = file.filename
        # 2. Вытаскиваем расширение (например, .pdf или .docx)
        ext = os.path.splitext(original_filename)[1]
        # 3. Генерируем уникальное имя для папки uploads
        safe_name = str(uuid.uuid4()) + ext
        # Сохраняем файл на диск под безопасным именем
        path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
        file.save(path)
        size = os.path.getsize(path)
        size_str = f"{size / 1024:.0f} Kb" if size < 1024*1024 else f"{size / (1024*1024):.2f} Mb"
        # 4. Записываем в базу оба имени
        new_file = File(
            filename=original_filename, # Русское имя для людей
            filepath=safe_name,         # Уникальное имя (uuid) для файловой системы
            file_size=size_str, 
            chapter_id=chapter_id,
            uploader_id=current_user.id
        )
        db.session.add(new_file)
        db.session.commit()
        uploaded_files_data.append({
            'id': new_file.id,
            'name': new_file.filename,
            'size': new_file.file_size,
            'role': current_user.role,
            'uploaderName': current_user.full_name,
            'canDownload': True,
            # ОБРАТИ ВНИМАНИЕ: Теперь мы передаем file_id для скачивания
            'downloadUrl': url_for('download', file_id=new_file.id),
            'deleteUrl': url_for('delete_file', file_id=new_file.id),
            'canDelete': True,
            'isApproved': False,
            # ДОБАВЛЯЕМ ФОРМАТИРОВАННУЮ ДАТУ:
            'uploadedAt': new_file.upload_date.strftime('%d.%m.%Y %H:%M')
        })
    return jsonify({'status': 'success', 'files': uploaded_files_data})

# --- ЗАГРУЗКА ФАЙЛОВ (Mentor и Intern) ---

@app.route('/download/<int:file_id>')
@login_required
def download(file_id):
    file_obj = File.query.get_or_404(file_id)
    
    # --- ЗАЩИТА ОТ СПИСЫВАНИЯ ---
    if current_user.role == 'intern':
        # Находим пользователя, который загрузил файл
        uploader = User.query.get(file_obj.uploader_id)
        # Если файл загрузил стажер, и этот стажер НЕ текущий пользователь:
        if uploader.role == 'intern' and file_obj.uploader_id != current_user.id:
            return "У вас нет прав для скачивания чужого домашнего задания!", 403
            
    # Если проверка пройдена (это наставник, или автор файла, или материал курса)
    return send_from_directory(
        app.config['UPLOAD_FOLDER'], 
        file_obj.filepath, 
        as_attachment=True,
        download_name=file_obj.filename 
    )

@app.route('/delete_file/<int:file_id>', methods=['GET', 'POST', 'DELETE'])
@login_required
def delete_file(file_id):
    file_obj = File.query.get_or_404(file_id)
    
    # Наставник удаляет все, Стажер только свои
    if current_user.role == 'mentor' or (current_user.role == 'intern' and file_obj.uploader_id == current_user.id):
        # Удаление физически с жесткого диска
        try:
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], file_obj.filepath)
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception as e:
            print(f"Ошибка удаления файла: {e}")
            
        db.session.delete(file_obj)
        db.session.commit()
        
        # Возвращаем JSON вместо redirect
        return jsonify({'status': 'success', 'msg': 'Файл удален'})
        
    return jsonify({'status': 'error', 'msg': 'Нет прав'}), 403

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/approve_file/<int:file_id>', methods=['POST'])
@login_required
def approve_file(file_id):
    # Только наставник может принимать работы
    if current_user.role != 'mentor':
        return jsonify({'status': 'error', 'msg': 'Нет прав'}), 403
        
    file_obj = File.query.get_or_404(file_id)
    file_obj.is_approved = True # Ставим галочку в базе
    db.session.commit()
    
    return jsonify({'status': 'success'})

# --- СКРИПТ ДЛЯ СОЗДАНИЯ БД И ТЕСТОВЫХ ДАННЫХ ---
# --- АДМИН ПАНЕЛЬ ---

# 1. Обнови свой /setup один раз, чтобы создать админа
@app.route('/setup')
def setup():
    with app.app_context():
        db.create_all()
        # Создаем Админа
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password='123', role='admin', full_name='Главный Администратор')
            db.session.add(admin)
            db.session.commit()
            
            # Если хочешь, можешь оставить тут создание тестовых наставников из старого кода
            
    return "База данных обновлена! Логин админа: admin / пароль: 123"

# 2. Главная страница админки
@app.route('/admin')
def admin_dashboard():
    # Проверка аутентификации и роли админа
    if not current_user.is_authenticated or current_user.role != 'admin':
        return "У вас нет доступа к этой странице", 403
        
    mentors = User.query.filter_by(role='mentor').all()
    interns = User.query.filter_by(role='intern').all()
    
    return render_template('admin.html', mentors=mentors, interns=interns)

# 3. Маршрут для создания пользователей


@app.route('/admin/create_user', methods=['POST'])
@login_required
def create_user():
    if current_user.role != 'admin': return "Доступ запрещен", 403
    
    username = request.form.get('username')
    password = request.form.get('password')
    full_name = request.form.get('full_name')
    role = request.form.get('role')
    mentor_id = request.form.get('mentor_id')

    # Проверка на дубликат логина
    if User.query.filter_by(username=username).first():
        flash('Пользователь с таким логином уже существует!', 'danger')
        return redirect(url_for('admin_dashboard'))

    new_user = User(username=username, password=password, full_name=full_name, role=role)
    
    # Привязываем наставника, если создаем стажера
    if role == 'intern' and mentor_id:
        new_user.mentor_id = int(mentor_id)

    db.session.add(new_user)
    db.session.commit()
    flash('Учетная запись успешно создана!', 'success')
    
    return redirect(url_for('admin_dashboard'))

# 4. Маршрут для удаления пользователей
@app.route('/admin/cleanup_files')
@login_required
def cleanup_orphaned_files():
    """Удаляет из БД записи о файлах, которых нет на диске"""
    if current_user.role != 'admin':
        return "Доступ запрещен", 403
    
    orphaned_files = []
    all_files = File.query.all()
    
    for file_obj in all_files:
        full_path = os.path.join(app.config['UPLOAD_FOLDER'], file_obj.filepath)
        if not os.path.exists(full_path):
            orphaned_files.append({
                'id': file_obj.id,
                'filename': file_obj.filename,
                'chapter_id': file_obj.chapter_id
            })
            db.session.delete(file_obj)
    
    db.session.commit()
    
    if orphaned_files:
        return jsonify({
            'status': 'success',
            'msg': f'Удалено {len(orphaned_files)} осиротевших записей',
            'deleted_files': orphaned_files
        })
    else:
        return jsonify({
            'status': 'success',
            'msg': 'Все файлы на месте, осиротевших записей не найдено'
        })

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin': return "Доступ запрещен", 403
    
    user_to_delete = User.query.get_or_404(user_id)
    
    if user_to_delete.role == 'admin':
        flash('Нельзя удалить администратора!', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    db.session.delete(user_to_delete)
    db.session.commit()
    
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(debug=True)