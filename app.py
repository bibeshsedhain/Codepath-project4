import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException

from signals import combine_signals, llm_signal, stylometric_signal

load_dotenv()


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def create_app(config=None, semantic_signal=None):
    app = Flask(__name__)
    app.config.update(DATABASE=os.getenv('DATABASE_PATH', 'instance/provenance.db'),
                      MAX_CONTENT_LENGTH=64 * 1024,
                      SUBMIT_LIMIT='10 per minute;100 per day',
                      RATELIMIT_STORAGE_URI=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'))
    if config:
        app.config.update(config)
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect():
        db = sqlite3.connect(app.config['DATABASE'], timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS content (
            id TEXT PRIMARY KEY, creator_id TEXT NOT NULL, text TEXT NOT NULL,
            status TEXT NOT NULL, decision TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS appeals (
            id TEXT PRIMARY KEY, content_id TEXT NOT NULL UNIQUE REFERENCES content(id),
            creator_id TEXT NOT NULL, reasoning TEXT NOT NULL, timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT NOT NULL REFERENCES content(id), event TEXT NOT NULL
        );
        ''')
    limiter = Limiter(get_remote_address, app=app, default_limits=[],
                      storage_uri=app.config['RATELIMIT_STORAGE_URI'], headers_enabled=True)
    semantic = semantic_signal or llm_signal

    def read_json():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError('The JSON body must be an object.')
        return value

    def required_string(data, key, minimum=1, maximum=100):
        value = data.get(key)
        if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
            raise ValueError(f'{key} must be a string of {minimum}–{maximum} characters.')
        return value.strip()

    def log(db, decision, event_type, **extra):
        event = {**decision, 'event_type': event_type, 'timestamp': now(), **extra}
        db.execute('INSERT INTO audit(content_id,event) VALUES (?,?)',
                   (decision['content_id'], json.dumps(event)))

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()  # Preserve Retry-After and other HTTP headers.
        response.data = app.json.dumps({'error': error.name, 'status': error.code})
        response.content_type = 'application/json'
        return response

    @app.get('/health')
    def health():
        return jsonify(status='ok', detection_mode='groq' if os.getenv('GROQ_API_KEY') else 'degraded_no_key')

    @app.post('/submit')
    @limiter.limit(lambda: app.config['SUBMIT_LIMIT'])
    def submit():
        data = read_json()
        text = required_string(data, 'text', 1, 20000)
        creator = required_string(data, 'creator_id')
        llm = semantic(text)
        style = stylometric_signal(text)
        result = {**combine_signals(llm, style), 'content_id': str(uuid4()),
                  'creator_id': creator, 'timestamp': now(), 'status': 'classified',
                  'signals': {'llm': llm, 'stylometric': style}}
        # Save the content and its first audit event together.
        with connect() as db:
            db.execute('INSERT INTO content VALUES (?,?,?,?,?)',
                       (result['content_id'], creator, text, 'classified', json.dumps(result)))
            log(db, result, 'classification')
        return jsonify(result)

    @app.get('/content/<content_id>')
    def get_content(content_id):
        with connect() as db:
            row = db.execute('SELECT * FROM content WHERE id=?', (content_id,)).fetchone()
        if row is None:
            return jsonify(error='Content not found.'), 404
        return jsonify({**json.loads(row['decision']), 'status': row['status']})

    @app.post('/appeal')
    def appeal():
        data = read_json()
        content_id = required_string(data, 'content_id')
        creator = required_string(data, 'creator_id')
        reasoning = required_string(data, 'creator_reasoning', 10, 2000)
        with connect() as db:
            # Take the write lock before checking for an existing appeal.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM content WHERE id=?', (content_id,)).fetchone()
            if row is None:
                return jsonify(error='Content not found.'), 404
            if creator != row['creator_id']:
                return jsonify(error='Creator does not match this submission.'), 403
            if db.execute('SELECT id FROM appeals WHERE content_id=?', (content_id,)).fetchone():
                return jsonify(error='An appeal already exists.'), 409
            appeal_id = str(uuid4())
            timestamp = now()
            db.execute('INSERT INTO appeals VALUES (?,?,?,?,?)', (appeal_id, content_id, creator, reasoning, timestamp))
            db.execute('UPDATE content SET status=? WHERE id=?', ('under_review', content_id))
            log(db, json.loads(row['decision']), 'appeal', status='under_review',
                appeal_id=appeal_id, appeal_reasoning=reasoning, appeal_timestamp=timestamp)
        return jsonify(appeal_id=appeal_id, content_id=content_id, status='under_review',
                       message='Appeal received. A human reviewer must review the original decision.')

    @app.get('/appeals')
    def appeals():
        with connect() as db:
            rows = db.execute('''SELECT appeals.*, content.text, content.decision, content.status
                                 FROM appeals JOIN content ON content.id=appeals.content_id
                                 ORDER BY appeals.timestamp DESC LIMIT 100''').fetchall()
        return jsonify(appeals=[{**dict(row), 'decision': json.loads(row['decision'])} for row in rows])

    @app.get('/log')
    def audit_log():
        try:
            limit = int(request.args.get('limit', 20))
        except ValueError:
            raise ValueError('limit must be an integer from 1 to 100.')
        if not 1 <= limit <= 100:
            raise ValueError('limit must be an integer from 1 to 100.')
        with connect() as db:
            rows = db.execute('SELECT sequence,event FROM audit ORDER BY sequence DESC LIMIT ?', (limit,)).fetchall()
        return jsonify(entries=[{**json.loads(row['event']), 'sequence': row['sequence']} for row in rows])

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000, debug=False)
