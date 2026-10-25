"""Reproduce integration evidence. Fixture mode never calls Groq.
Run python verify.py --live after configuring .env to assess all four texts live.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from app import create_app
from samples import SAMPLES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if args.live and os.getenv('GROQ_API_KEY', '') in ('', 'your_key_here'):
        parser.error('Set a real GROQ_API_KEY in .env before live evaluation.')
    scores = iter([0.99, 0.05, 0.70, 0.60])
    def fixture(_):
        return {'score': next(scores), 'available': True, 'model': 'TEST_FIXTURE',
                'reasoning': 'Injected test value; not a live model assessment.'}
    with tempfile.TemporaryDirectory() as directory:
        config = {'TESTING': True, 'DATABASE': str(Path(directory) / 'verify.db')}
        app = create_app(config, semantic_signal=None if args.live else fixture)
        client = app.test_client()
        results = []
        for name, text in SAMPLES.items():
            response = client.post('/submit', json={'text': text, 'creator_id': 'demo-writer'})
            assert response.status_code == 200, response.json
            results.append({'sample': name, 'text': text, 'response': response.json})
        appeal = client.post('/appeal', json={'content_id': results[0]['response']['content_id'],
            'creator_id': 'demo-writer', 'creator_reasoning': 'I wrote this myself; please review my drafts.'})
        assert appeal.status_code == 200
        evidence = {'mode': 'live_groq' if args.live else 'fixture_integration_not_live_detection',
                    'submissions': results, 'appeal': appeal.json,
                    'audit_entries': client.get('/log').json['entries']}
        # Independent fresh limiter, deterministic neutral semantic result; no paid API calls.
        limiter_app = create_app({'TESTING': True, 'DATABASE': str(Path(directory) / 'limit.db')},
            semantic_signal=lambda _: {'score': 0.5, 'available': True, 'model': 'TEST_FIXTURE', 'reasoning': 'Rate limit test'})
        limiter_client = limiter_app.test_client()
        evidence['rate_limit_statuses'] = [limiter_client.post('/submit', json={
            'text': SAMPLES['uniform_ai_like'], 'creator_id': 'limit-test'}).status_code for _ in range(12)]
        assert evidence['rate_limit_statuses'] == [200]*10 + [429]*2
        out = Path('evidence/live_run.json' if args.live else 'evidence/fixture_run.json')
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(evidence, indent=2) + '\n')
        print(f'Wrote {out}; mode={evidence["mode"]}')
        for item in results:
            r = item['response']
            print(f'{item["sample"]}: AI score={r["ai_score"]}, confidence={r["confidence"]}, {r["attribution"]}')
        print('Rate limit:', ' '.join(map(str, evidence['rate_limit_statuses'])))


if __name__ == '__main__':
    main()
