from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
import qrcode
import os
import uuid
import hashlib
from escpos.printer import Usb
from datetime import datetime

# Dirección IP del servidor
SERVER_HOST = '192.168.0.224'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///queue.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
socketio = SocketIO(app)

class QueueEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(3), unique=True, nullable=False)
    hash = db.Column(db.String(64), nullable=False)
    token = db.Column(db.String(36), unique=True, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='active')

def generate_number_hash(number):
    return hashlib.sha256(number.encode()).hexdigest()

def get_next_number():
    last_entry = QueueEntry.query.order_by(QueueEntry.id.desc()).first()
    if last_entry:
        try:
            last_number = int(last_entry.number)
            return f"{last_number + 1:03d}"
        except ValueError:
            return '001'
    else:
        return '001'

@app.route('/')
def index():
    print(f"[DEBUG] Accediendo a /index")
    return render_template('index.html')

@app.route('/join', methods=['GET', 'POST'])
def join_queue():
    if request.method == 'POST':
        formatted_number = get_next_number()
        number_hash = generate_number_hash(formatted_number)
        token = str(uuid.uuid4())
        new_entry = QueueEntry(number=formatted_number, hash=number_hash, token=token, status='active')
        db.session.add(new_entry)
        db.session.commit()
        print(f"[DEBUG] Añadido a la cola desde /join: number={formatted_number}, hash={number_hash}, token={token}, status=active")
        active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
        socketio.emit('queue_updated', {
            'queue': [{'number': entry.number, 'hash': entry.hash, 'status': entry.status} for entry in active_queue],
            'current': active_queue[0].number if active_queue else None
        })
        print(f"[DEBUG] Emitiendo queue_updated desde /join: queue={active_queue}, current={active_queue[0].number if active_queue else None}")
        flash((f"Tu número es {formatted_number}. Por favor, espera tu turno.", number_hash), 'success')
        print(f"[DEBUG] Redirigiendo desde /join a /status/{number_hash}")
        return redirect(url_for('status', hash=number_hash))
    print(f"[DEBUG] Accediendo a /join (GET)")
    return render_template('join.html')

@app.route('/print', methods=['POST'])
def print_queue_number():
    formatted_number = get_next_number()
    number_hash = generate_number_hash(formatted_number)
    token = str(uuid.uuid4())
    new_entry = QueueEntry(number=formatted_number, hash=number_hash, token=token, status='active')
    db.session.add(new_entry)
    db.session.commit()
    print(f"[DEBUG] Añadido a la cola desde /print: number={formatted_number}, hash={number_hash}, token={token}, status=active")
    active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
    socketio.emit('queue_updated', {
        'queue': [{'number': entry.number, 'hash': entry.hash, 'status': entry.status} for entry in active_queue],
        'current': active_queue[0].number if active_queue else None
    })
    print(f"[DEBUG] Emitiendo queue_updated desde /print: queue={active_queue}, current={active_queue[0].number if active_queue else None}")
    try:
        print_ticket(formatted_number)
        print(f"[DEBUG] Ticket impreso para number={formatted_number}")
        flash((f"Tu número es {formatted_number}. Por favor, espera tu turno.", number_hash), 'success')
    except Exception as e:
        print(f"[DEBUG] Error al imprimir: {e}")
        flash((f"Tu número es {formatted_number}. Por favor, espera tu turno. (Error al imprimir, toma nota del número)", number_hash), 'error')
    print(f"[DEBUG] Redirigiendo desde /print a /")
    return redirect(url_for('index'))

@app.route('/status/<hash>')
def status(hash):
    print(f"[DEBUG] Accediendo a /status/{hash}")
    entry = QueueEntry.query.filter_by(hash=hash).first()
    if entry:
        if entry.status == 'active':
            active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
            position = next((i for i, e in enumerate(active_queue) if e.id == entry.id), None)
            if position is not None:
                position += 1
            else:
                position = None
        else:
            position = None
        return render_template('status.html', number=entry.number, status=entry.status, position=position, hash=hash)
    else:
        return render_template('status.html', number=None, status='invalid', position=None, hash=hash)

@app.route('/status')
def status_data():
    print(f"[DEBUG] Accediendo a /status (API)")
    active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
    return jsonify({
        'queue': [{'number': entry.number, 'hash': entry.hash, 'status': entry.status} for entry in active_queue],
        'current': active_queue[0].number if active_queue else None
    })

@app.route('/display')
def display():
    print(f"[DEBUG] Accediendo a /display")
    return render_template('display.html')

@app.route('/public')
def public():
    print(f"[DEBUG] Accediendo a /public")
    return render_template('public.html')

@app.route('/next', methods=['POST'])
def next_in_queue():
    first_entry = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).first()
    if first_entry:
        first_entry.status = 'called'
        db.session.commit()
        print(f"[DEBUG] Marcado como llamado: number={first_entry.number}, hash={first_entry.hash}, status=called")
        active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
        socketio.emit('queue_updated', {
            'queue': [{'number': entry.number, 'hash': entry.hash, 'status': entry.status} for entry in active_queue],
            'current': active_queue[0].number if active_queue else None
        })
        print(f"[DEBUG] Emitiendo queue_updated desde /next: queue={active_queue}, current={active_queue[0].number if active_queue else None}")
    return jsonify({'status': 'success'})

@app.route('/remove/<number>', methods=['POST'])
def remove_from_queue(number):
    entry = QueueEntry.query.filter_by(number=number).first()
    if entry:
        entry.status = 'removed'
        db.session.commit()
        print(f"[DEBUG] Marcado como eliminado: number={number}, hash={entry.hash}, status=removed")
        active_queue = QueueEntry.query.filter_by(status='active').order_by(QueueEntry.id).all()
        socketio.emit('queue_updated', {
            'queue': [{'number': entry.number, 'hash': entry.hash, 'status': entry.status} for entry in active_queue],
            'current': active_queue[0].number if active_queue else None
        })
        socketio.emit('number_removed', {'number': number, 'hash': entry.hash})
        print(f"[DEBUG] Emitiendo number_removed: number={number}, hash={entry.hash}")
        return jsonify({'status': 'success', 'message': f'Número {number} eliminado de la cola.'})
    else:
        print(f"[DEBUG] Intento de eliminar: number={number}, no encontrado")
        return jsonify({'status': 'error', 'message': f'Número {number} no encontrado en la cola.'})

@app.route('/reset_queue', methods=['POST'])
def reset_queue():
    # Eliminar todas las entradas de la cola
    db.session.query(QueueEntry).delete()
    db.session.commit()
    print("[DEBUG] Cola reseteada a 000")
    socketio.emit('queue_updated', {'queue': [], 'current': None})
    return jsonify({'status': 'success', 'message': 'Cola reseteada a 000'})

def generate_qr_code():
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f'http://{SERVER_HOST}:5000/join')
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    img.save('static/qr_code.png')

def print_ticket(number):
    try:
        printer = Usb(0x04B8, 0x0E15)  # VID y PID para Epson TM-T20
        printer.set(align='center', font='a', height=2, width=2)
        printer.text("Sistema de Colas\n")
        printer.text(f"Número: {number}\n")
        printer.set(align='center', font='a', height=1, width=1)
        printer.text("Por favor, espera tu turno.\n")
        printer.text(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        printer.cut()
        printer.close()
    except Exception as e:
        print(f"[DEBUG] Error al imprimir: {e}")
        raise e

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    os.makedirs('static', exist_ok=True)
    generate_qr_code()
    socketio.run(app, host='0.0.0.0', port=5000)