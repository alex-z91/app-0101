import os, sqlite3, uuid, secrets, time, mimetypes
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, abort, send_file
from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
UPLOADS = Path(os.environ.get('UPLOAD_DIR', ROOT / 'uploads'))
DATA.mkdir(parents=True, exist_ok=True); UPLOADS.mkdir(parents=True, exist_ok=True)
DB = DATA/'rifa.sqlite3'
app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY', secrets.token_hex(32)), MAX_CONTENT_LENGTH=5*1024*1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','0')=='1')
CAMPAIGN_ENABLED = os.environ.get('CAMPAIGN_ENABLED','0')=='1'
WHATSAPP = ''.join(x for x in os.environ.get('MOTHER_WHATSAPP','') if x.isdigit())
PIX_KEY = os.environ.get('PIX_KEY','91984535023')
PIX_NAME = os.environ.get('PIX_NAME','Edna Magalhães Uchoa')
ADMIN_HASH = os.environ.get('ADMIN_PASSWORD_HASH','')
RESERVATION_SECONDS = 15*60

@app.after_request
def secure_headers(resp):
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    resp.headers['Cache-Control']='no-store' if request.path.startswith('/api/') or request.path.startswith('/admin') else 'public, max-age=120'
    return resp

def conn():
    db=sqlite3.connect(DB,timeout=20,isolation_level=None); db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA busy_timeout=5000')
    return db

def initialize():
    with conn() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, name TEXT NOT NULL, phone TEXT NOT NULL, numbers TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, receipt_file TEXT, receipt_type TEXT, receipt_original TEXT, confirmed_at INTEGER);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE TABLE IF NOT EXISTS numbers (number INTEGER PRIMARY KEY CHECK(number BETWEEN 1 AND 100), order_id TEXT NOT NULL, FOREIGN KEY(order_id) REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, order_id TEXT NOT NULL, action TEXT NOT NULL);
        ''')
initialize()

def cleanup(db, now):
    expired=db.execute("SELECT id FROM orders WHERE status='reserved' AND expires_at<=?",(now,)).fetchall()
    for order in expired:
        db.execute('DELETE FROM numbers WHERE order_id=?',(order['id'],))
        db.execute("UPDATE orders SET status='expired' WHERE id=?",(order['id'],))
        db.execute('INSERT INTO audit(ts,order_id,action) VALUES(?,?,?)',(now,order['id'],'expired'))

def csrf():
    if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(28)
    return session['csrf']

def verify_csrf():
    if not secrets.compare_digest((request.headers.get('X-CSRF-Token') or request.form.get('csrf') or ''), csrf()): abort(403)

@app.get('/')
def home():
    return render_template('index.html',pix_key=PIX_KEY,pix_name=PIX_NAME,whatsapp=WHATSAPP,enabled=CAMPAIGN_ENABLED,csrf=csrf())

@app.get('/api/status')
def status():
    db=conn(); now=int(time.time())
    try:
        db.execute('BEGIN IMMEDIATE'); cleanup(db,now)
        rows=db.execute('SELECT n.number,o.status FROM numbers n JOIN orders o ON o.id=n.order_id').fetchall()
        db.commit()
        return jsonify(numbers={str(r['number']):r['status'] for r in rows}, sold=sum(r['status']=='paid' for r in rows), occupied=len(rows), enabled=CAMPAIGN_ENABLED)
    finally: db.close()

@app.post('/api/orders')
def create_order():
    verify_csrf()
    if not CAMPAIGN_ENABLED: return jsonify(error='Campanha em modo de demonstração. Reservas e pagamentos não estão habilitados.'),403
    payload=request.get_json(silent=True) or {}
    nums=payload.get('numbers',[]); name=str(payload.get('name','')).strip(); phone=''.join(x for x in str(payload.get('phone','')) if x.isdigit())
    if not isinstance(nums,list) or not 1<=len(nums)<=100 or any(type(n) is not int or n<1 or n>100 for n in nums) or len(set(nums))!=len(nums):return jsonify(error='Seleção de números inválida.'),400
    if not 3<=len(name)<=90 or not 10<=len(phone)<=13:return jsonify(error='Informe nome e telefone válidos.'),400
    nums=sorted(nums); now=int(time.time()); oid=uuid.uuid4().hex; token=secrets.token_urlsafe(24)
    db=conn()
    try:
        db.execute('BEGIN IMMEDIATE'); cleanup(db,now)
        existing=db.execute('SELECT number FROM numbers WHERE number IN ('+','.join('?'*len(nums))+')', nums).fetchall()
        if existing:
            db.rollback();return jsonify(error='Alguns números acabaram de ser reservados. Atualize a cartela.',unavailable=[r['number'] for r in existing]),409
        db.execute('INSERT INTO orders(id,token,name,phone,numbers,amount,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',(oid,token,name,phone,','.join(map(str,nums)),len(nums)*2000,'reserved',now,now+RESERVATION_SECONDS))
        db.executemany('INSERT INTO numbers(number,order_id) VALUES(?,?)',[(n,oid) for n in nums])
        db.execute('INSERT INTO audit(ts,order_id,action) VALUES(?,?,?)',(now,oid,'reserved'))
        db.commit()
    finally:db.close()
    return jsonify(order_id=oid,token=token,expires_at=now+RESERVATION_SECONDS,numbers=nums,amount=len(nums)*20,pix_key=PIX_KEY,pix_name=PIX_NAME,whatsapp=WHATSAPP),201

@app.post('/api/orders/<oid>/receipt')
def upload_receipt(oid):
    verify_csrf()
    token=request.form.get('token','')
    if not token:return jsonify(error='Protocolo inválido.'),403
    f=request.files.get('receipt')
    if not f:return jsonify(error='Selecione um comprovante.'),400
    data=f.read(5*1024*1024+1)
    if len(data)>5*1024*1024 or len(data)<50:return jsonify(error='Arquivo inválido ou maior que 5 MB.'),400
    if data.startswith(b'%PDF-'): ext,ctype='.pdf','application/pdf'
    elif data.startswith(b'\xff\xd8\xff'): ext,ctype='.jpg','image/jpeg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):ext,ctype='.png','image/png'
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP':ext,ctype='.webp','image/webp'
    else:return jsonify(error='Use PDF, JPG, PNG ou WEBP.'),400
    db=conn(); now=int(time.time()); destination=None
    try:
        db.execute('BEGIN IMMEDIATE'); cleanup(db,now)
        order=db.execute('SELECT * FROM orders WHERE id=? AND token=?',(oid,token)).fetchone()
        if not order or order['status'] not in ('reserved','review'):db.rollback();return jsonify(error='Reserva não encontrada ou encerrada.'),404
        destination=secrets.token_hex(20)+ext
        (UPLOADS/destination).write_bytes(data)
        old=order['receipt_file']
        db.execute("UPDATE orders SET receipt_file=?,receipt_type=?,receipt_original=?,status='review',expires_at=? WHERE id=?",(destination,ctype,(f.filename or 'comprovante')[:120],now+48*3600,oid))
        db.execute('INSERT INTO audit(ts,order_id,action) VALUES(?,?,?)',(now,oid,'receipt_uploaded'))
        db.commit()
        if old:
            try:(UPLOADS/old).unlink(missing_ok=True)
            except OSError:pass
    except Exception:
        db.rollback()
        if destination:(UPLOADS/destination).unlink(missing_ok=True)
        raise
    finally:db.close()
    return jsonify(ok=True,message='Comprovante recebido. Aguardando conferência manual.'),200

@app.get('/api/orders/<oid>')
def get_order(oid):
    token=request.args.get('token','')
    with conn() as db:
        row=db.execute('SELECT id,numbers,amount,status,created_at,expires_at,receipt_file FROM orders WHERE id=? AND token=?',(oid,token)).fetchone()
    if not row:abort(404)
    return jsonify(id=row['id'],numbers=[int(x) for x in row['numbers'].split(',')],amount=row['amount']/100,status=row['status'],expires_at=row['expires_at'],has_receipt=bool(row['receipt_file']))

def admin_only(fn):
    @wraps(fn)
    def inner(*args,**kwargs):
        if not session.get('admin'):return redirect(url_for('login')) if request.method=='GET' else (jsonify(error='Acesso negado.'),401)
        return fn(*args,**kwargs)
    return inner

@app.route('/admin/login',methods=['GET','POST'])
def login():
    error=None
    if request.method=='POST':
        verify_csrf()
        if not ADMIN_HASH:error='Configure ADMIN_PASSWORD_HASH no servidor antes de acessar.'
        elif check_password_hash(ADMIN_HASH,request.form.get('password','')):session.clear();session['admin']=True;csrf();return redirect(url_for('admin'))
        else:time.sleep(0.5);error='Senha incorreta.'
    return render_template('login.html',error=error,csrf=csrf())

@app.post('/admin/logout')
@admin_only
def logout():
    verify_csrf();session.clear();return redirect(url_for('home'))

@app.get('/admin')
@admin_only
def admin():
    with conn() as db:
        db.execute('BEGIN IMMEDIATE');cleanup(db,int(time.time()))
        orders=db.execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()
        paid=db.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid'").fetchone()[0]
        db.commit()
    return render_template('admin.html',orders=orders,paid=paid/100,csrf=csrf())

@app.get('/admin/receipts/<oid>')
@admin_only
def receipt(oid):
    with conn() as db:row=db.execute('SELECT receipt_file,receipt_type FROM orders WHERE id=?',(oid,)).fetchone()
    if not row or not row['receipt_file']:abort(404)
    return send_file(UPLOADS/row['receipt_file'],mimetype=row['receipt_type'],as_attachment=True,download_name='comprovante-'+oid[:8]+({'application/pdf':'.pdf','image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[row['receipt_type']]))

@app.post('/admin/orders/<oid>/<action>')
@admin_only
def update_order(oid,action):
    verify_csrf()
    if action not in ('paid','cancelled'):abort(400)
    db=conn();now=int(time.time())
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()
        if not row or row['status'] not in ('review','reserved'):
            db.rollback();return redirect(url_for('admin'))
        if action=='cancelled':db.execute('DELETE FROM numbers WHERE order_id=?',(oid,))
        db.execute('UPDATE orders SET status=?,confirmed_at=? WHERE id=?',(action,now,oid))
        db.execute('INSERT INTO audit(ts,order_id,action) VALUES(?,?,?)',(now,oid,action));db.commit()
    finally:db.close()
    return redirect(url_for('admin'))

if __name__=='__main__':app.run(debug=False)