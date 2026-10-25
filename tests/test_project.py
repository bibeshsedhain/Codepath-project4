import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from samples import SAMPLES
from signals import LABELS, combine_signals, llm_signal, stylometric_signal


def fake_score(score):
    return {'score': score, 'available': True, 'model': 'TEST_FIXTURE',
            'reasoning': 'Injected test value; not a live model assessment.'}


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATABASE': str(Path(self.directory.name) / 'test.db')}
        self.app = create_app(self.config, semantic_signal=lambda _: fake_score(0.99))
        self.client = self.app.test_client()

    def tearDown(self):
        self.directory.cleanup()

    def submit(self, text=None, client=None):
        return (client or self.client).post('/submit', json={
            'text': text or SAMPLES['uniform_ai_like'], 'creator_id': 'writer-1'})

    def test_all_labels_through_real_routes(self):
        for name, score, expected in [
            ('uniform_ai_like', 0.99, 'likely_ai'),
            ('casual_human_like', 0.05, 'likely_human'),
            ('edited_borderline', 0.60, 'uncertain')]:
            with self.subTest(name=name):
                app = create_app(self.config, semantic_signal=lambda _, s=score: fake_score(s))
                response = self.submit(SAMPLES[name], app.test_client())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['attribution'], expected)
                self.assertEqual(response.json['label'], LABELS[expected])
                self.assertEqual(len(response.json['signals']), 2)

    def test_appeal_preserves_original_and_persists_status(self):
        decision = self.submit().json
        payload = {'content_id': decision['content_id'], 'creator_id': 'writer-1',
                   'creator_reasoning': 'I wrote this myself and can provide earlier drafts.'}
        self.assertEqual(self.client.post('/appeal', json={**payload, 'creator_id': 'other'}).status_code, 403)
        self.assertEqual(self.client.post('/appeal', json=payload).status_code, 200)
        self.assertEqual(self.client.post('/appeal', json=payload).status_code, 409)
        fresh = create_app(self.config).test_client()
        self.assertEqual(fresh.get('/content/' + decision['content_id']).json['status'], 'under_review')
        logs = fresh.get('/log').json['entries']
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]['appeal_reasoning'], payload['creator_reasoning'])
        self.assertEqual(logs[1]['status'], 'classified')
        self.assertEqual(logs[0]['confidence'], logs[1]['confidence'])
        self.assertEqual(len(fresh.get('/appeals').json['appeals']), 1)

    def test_rate_limit(self):
        statuses = [self.submit().status_code for _ in range(12)]
        self.assertEqual(statuses, [200] * 10 + [429] * 2)
        self.assertEqual(len(self.client.get('/log').json['entries']), 10)

    def test_invalid_and_missing_inputs(self):
        for data in [{}, [], {'text': 2, 'creator_id': 'x'}, {'text': ' ', 'creator_id': 'x'}]:
            self.assertEqual(self.client.post('/submit', json=data).status_code, 400)
        self.assertEqual(self.client.post('/submit', data='plain').status_code, 415)
        self.assertEqual(self.client.post('/submit', data='x' * 70000, content_type='application/json').status_code, 413)
        self.assertEqual(self.client.get('/content/missing').status_code, 404)
        self.assertEqual(self.client.post('/appeal', json={'content_id': 'missing', 'creator_id': 'x',
                         'creator_reasoning': 'Please review this decision.'}).status_code, 404)
        self.assertEqual(self.client.get('/log?limit=bad').status_code, 400)
        self.assertEqual(self.client.get('/log?limit=101').status_code, 400)

    def test_guardrails_and_exact_thresholds(self):
        style = {'score': 0.8, 'metrics': {'word_count': 80, 'sentence_count': 4}}
        self.assertEqual(combine_signals(fake_score(0.9), style)['attribution'], 'likely_ai')
        style['score'] = 0.2
        self.assertEqual(combine_signals(fake_score(0.2), style)['attribution'], 'likely_human')
        style['score'] = 0.1
        result = combine_signals(fake_score(0.99), style)
        self.assertEqual(result['attribution'], 'uncertain')
        self.assertIn('signals_disagree', result['guardrails'])
        style['metrics']['word_count'] = 5
        self.assertEqual(combine_signals(fake_score(0.05), style)['attribution'], 'uncertain')
        style['metrics']['word_count'] = 80
        self.assertEqual(combine_signals({**fake_score(.5), 'available': False}, style)['attribution'], 'uncertain')

    def test_provider_json_validation_and_failure(self):
        class FakeClient:
            def __init__(self, content):
                self.content = content
                self.chat = SimpleNamespace(completions=self)
            def create(self, **kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])
        self.assertTrue(llm_signal('text', FakeClient('{"score":0.4,"reasoning":"Weak evidence"}'))['available'])
        for raw in ['bad json', '{"score":2,"reasoning":"oops"}', '{"score":true}',
                    '{"score":NaN,"reasoning":"oops"}', '{"score":0.2,"reasoning":[]}']:
            self.assertFalse(llm_signal('text', FakeClient(raw))['available'])
        with patch.dict('os.environ', {'GROQ_API_KEY': ''}):
            app = create_app(self.config)
            result = self.submit(client=app.test_client()).json
            self.assertFalse(result['signals']['llm']['available'])
            self.assertEqual(result['attribution'], 'uncertain')

    def test_structural_metrics_are_finite(self):
        for text in ['', '...', *SAMPLES.values()]:
            self.assertTrue(0 <= stylometric_signal(text)['score'] <= 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
