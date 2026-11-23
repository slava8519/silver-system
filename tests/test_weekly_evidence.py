from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from second_brain import storage
from second_brain.cycles import Cycles
from second_brain.gantt import Gantt
from second_brain.planning import Planning
from second_brain.schedule import Schedule
from second_brain.weekly_evidence import build_weekly_evidence, _recorded


class WeeklyEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = storage.initialize(Path(temp.name) / 'Vault')
        self.planning = Planning(self.root)
        self.plan = self.planning.create_plan('Цикл')
        self.task = self.planning.create_task('Проверить', due='2026-11-25')
        self.planning.add_task(self.plan['id'], self.task['id'])
        self.note = storage.create(self.root, 'note', 'Свидетельство', body='Проверка назначена.\nПроверка перенесена.')
        self.line = storage.read_record(self.note)['body_start']

    def event(self, stamp, offset=0, entry_id=None, event_date='2026-11-20'):
        gantt = Gantt(self.root)
        data = {'id': self.task['id'], 'field': 'latest', 'date': event_date, 'actor': 'ai',
                'revision': gantt.detail(self.task['id'])['revision'],
                'sources': [{'path': 'Notes/Свидетельство.md', 'line': self.line + offset}],
                'text': self.note.read_text().splitlines()[self.line + offset - 1]}
        with patch('second_brain.gantt.datetime') as clock:
            clock.now.return_value = datetime.fromisoformat(stamp)
            return gantt.edit({**data, 'entryId': entry_id})['latest']

    def close(self, stamp='2026-11-22T22:30:00.500000+00:00'):
        cycles = Cycles(self.root)
        preview = cycles.preview(self.plan['id'])
        with patch('second_brain.cycles.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(stamp)
            return cycles.close(self.plan['id'], preview['revision'])

    def review(self, **options):
        return build_weekly_evidence(self.root, self.plan['id'], timezone_name='Europe/Minsk', **options)

    def test_frozen_state_and_real_source_for_event_even_if_event_is_later_rewritten(self):
        event, = self.event('2026-11-22T22:30:00.100000+00:00')
        self.close()
        self.event('2026-11-22T22:30:00.800000+00:00', offset=1, entry_id=event['id'])
        moved = self.note.rename(self.note.with_name('Архив.md'))
        moved.write_text(moved.read_text().replace('Проверка назначена.', 'Вводная.\nПроверка назначена.'))
        task_file = self.root / self.task['path']
        task_file.write_text(task_file.read_text().replace('2026-11-25', '2027-01-01'))
        task_file.rename(task_file.with_name('Новый путь.md'))
        result = self.review()
        self.assertEqual(result['warnings'], [])
        self.assertEqual((result['start'], result['end']), ('2026-11-17', '2026-11-23'))
        snapshot, event = result['facts']
        self.assertEqual(snapshot['dates'], {'start': None, 'due': '2026-11-25', 'forecast': None, 'completed': None})
        self.assertIn('2026-11-25', (self.root / self.plan['path']).read_text().splitlines()[snapshot['sources'][0]['line'] - 1])
        self.assertEqual((event['text'], event['date']), ('Проверка назначена.', '2026-11-20'))
        ref, = event['sources']
        self.assertEqual((self.root / ref['path'], ref['line']), (moved, self.line + 1))
        self.assertIn('line=' + str(ref['line']), ref['href'])
        moved.write_text(moved.read_text().replace('Проверка назначена.', 'Запись изменена.'))
        result = self.review()
        self.assertEqual([v['kind'] for v in result['facts']], ['snapshot'])
        self.assertTrue(result['warnings'])

    def test_unknown_native_change_does_not_restore_removed_managed_event(self):
        self.event('2026-11-21T11:00:00+00:00')
        path = self.root / self.task['path']
        path.write_text(path.read_text().replace('\nПроверка назначена.\n', '\nФормулировка владельца.\n'))
        self.close()
        self.event('2026-11-22T22:30:00.800000+00:00', offset=1)
        result = self.review()
        self.assertEqual([e['kind'] for e in result['facts']], ['snapshot'])
        self.assertTrue(result['warnings'])

    def test_period_cutoff_and_snapshot_do_not_infer_completion(self):
        with self.assertRaisesRegex(ValueError, 'закрытый'):
            self.review()
        self.event('2026-11-21T20:00:00+00:00', event_date='2026-11-15')
        self.event('2026-11-22T12:30:00+00:00', offset=1, event_date='2026-11-22')
        self.close()
        self.event('2026-11-22T22:30:00.700000+00:00', event_date='2026-11-19')
        result = self.review(start='2026-11-16', end='2026-11-22')
        self.assertEqual([e['date'] for e in result['facts'] if e['kind'] == 'event'], ['2026-11-22'])
        self.assertIsNone(result['facts'][0]['dates']['completed'])
        with self.assertRaises(ValueError):
            self.review(end='2026-11-24')
        with self.assertRaises(ValueError):
            self.review(start='2026-11-09')
        with self.assertRaises(ValueError):
            build_weekly_evidence(self.root, self.plan['id'], timezone_name='Bad/Zone')
        path = self.root / self.task['path']
        path.unlink()
        result = self.review()
        self.assertEqual([e['kind'] for e in result['facts']], ['snapshot'])
        self.assertIn(self.task['title'], result['warnings'][0]['text'])
        self.assertEqual(result['warnings'][0]['sources'], result['facts'][0]['sources'])

    def test_completion_uses_actual_date_not_date_author_wrote_the_record(self):
        schedule = Schedule(self.root)
        options = {'actor': 'ai', 'sources': [{'path': 'Notes/Свидетельство.md', 'line': self.line}]}
        self.note.write_text(self.note.read_text().replace('Проверка назначена.', 'Работа завершена 4 марта.'))
        proposal = schedule.preview(self.task['id'], 'completed', '2020-03-04', **options)
        with patch('second_brain.schedule.datetime') as clock:
            clock.now.return_value = datetime.fromisoformat('2020-03-08T12:00:00+00:00')
            schedule.apply(self.task['id'], 'completed', '2020-03-04', proposal['revision'], **options)
        self.close('2020-03-08T18:00:00.100000+00:00')
        result = self.review(start='2020-03-02', end='2020-03-08')
        snapshot, event = result['facts']
        self.assertEqual((snapshot['dates']['completed'], event['date']), ('2020-03-04', '2020-03-04'))
        row, proof = event['sources']
        self.assertEqual(row, snapshot['sources'][0])
        self.assertEqual((proof['line'], proof['quote']), (self.line, 'Работа завершена 4 марта.'))
        later = self.review(start='2020-03-05', end='2020-03-08')
        self.assertEqual([fact['kind'] for fact in later['facts']], ['snapshot'])

    def test_legacy_second_precision_boundaries(self):
        for precise_close in (False, True):
            stamp = '2026-11-22T23:30:00.500000+03:00' if precise_close else '2026-11-22T23:30:00+03:00'
            cutoff = datetime.fromisoformat(stamp), precise_close
            self.assertIsNone(_recorded('2026-11-22T20:30:00+00:00', cutoff))
            self.assertTrue(_recorded('2026-11-22T20:30:00+00:00', cutoff, unchanged=True))
            self.assertTrue(_recorded('2026-11-22T20:29:59+00:00', cutoff))
            self.assertFalse(_recorded('2026-11-22T20:30:01+00:00', cutoff))
            self.assertIs(_recorded('2026-11-22T20:30:00.100000+00:00', cutoff), True if precise_close else None)
