"""The PO's bounded board snapshot, its prompt routes, and advisory archive ask."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import board_brief
import dashboard as d
import rotation
from test_rotation_ask import _Base
from test_quiet_restart import _Hub
import test_terminal_input
from types import SimpleNamespace


class Brief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hp = Path(self.tmp.name) / 'PO-HANDOVER.md'
        self.now = time.mktime((2026, 9, 30, 12, 0, 0, 0, 0, -1))
        self.project = dict(id='p', poRoomId='po', path=self.tmp.name, isGit=True)
        self.rooms = []
        self.points = []
        self.attention = {}
        self.usage = {'sources': []}
        patches = [
            mock.patch.object(d, 'load_session_projects', return_value={}),
            mock.patch.object(d, 'load_labels', return_value={}),
            mock.patch.object(d.chatroom, 'list_rooms', side_effect=lambda: self.rooms),
            mock.patch.object(d, '_room_is_live', side_effect=lambda r: r.get('live', True)),
            mock.patch.object(d.attention, 'by_room', side_effect=lambda: self.attention),
            mock.patch.object(d.attention, 'open_ask', side_effect=lambda r: r.get('ask')),
            mock.patch.object(d.chatroom, 'last_real_report', side_effect=lambda r: r.get('report', {})),
            mock.patch.object(d.points, 'load', side_effect=lambda rid: {'points': self.points}),
            mock.patch.object(d.rotation, 'handover_path', return_value=self.hp),
            mock.patch.object(d.due, '_load', return_value={'seen': {}}),
            mock.patch.object(d.usage, 'snapshot', side_effect=lambda: self.usage),
            mock.patch.object(d, '_git_out', side_effect=self.git),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def git(self, cwd, *args, **kwargs):
        if args[0] == 'rev-list':
            return '0' if args[-1] == 'main..merged' else '2'
        if args[0] == 'log':
            return '\n'.join(f'abc{i} Merge #{i}: work' for i in range(10, 15))
        return ''

    def room(self, no, **kw):
        r = dict(id=f'r{no}', no=no, projectId='p', title=f'Task {no}',
                 status='active', workflow='inprogress', workspace={'branch': f'sess/ED-{no}'})
        r.update(kw)
        self.rooms.append(r)
        return r

    def build(self):
        return board_brief.build(d, self.project, now=self.now)

    def test_all_sources_and_priority_and_scope(self):
        self.room(1)
        self.room(2, participants=[{'review': {'startedAt': 1}}])
        self.room(3, ask={'to': 'po'})
        self.room(4, ask={'to': 'user'})
        self.room(5)
        self.attention['r5'] = {'state': 'stalled'}
        self.room(6, report={'kind': 'completed'}, live=False)
        self.room(7, report={'kind': 'completed'}, workspace={'branch': 'merged'})
        self.room(8, launched=False, priority=4)
        self.room(9, launched=False, priority=1, after=['#7'])
        self.room(99, projectId='other')
        self.rooms.append(dict(id='po', projectId='p'))
        self.points = [dict(id='P1', state='open', text='Do this'),
                       dict(id='P2', state='delivered', text='Confirm that'),
                       dict(id='P3', state='acknowledged', text='old')]
        self.hp.write_text('## Due\n- 09-30 14:00 — Future\n- 09-30 10:00 — Past\n'
                           '- [x] 09-30 15:00 — Finished\n', encoding='utf-8')
        os.utime(self.hp, (self.now - 3600, self.now - 3600))
        self.usage = {'sources': [{'source': 'codex', 'windows': [
            dict(label='5h', percent=12, trusted=False, resetsAt='2026-09-30T14:00:00+00:00'),
            dict(label='7d', percent=None, stalePercent=95, trusted=False),
            dict(label='reserve', percent=100, inUse=False)]}]}
        text = self.build()
        for part in ('working', 'waiting on reviewer', 'waiting on the PO', 'waiting on the CEO',
                     'stalled', '2 ahead of main', '#6 Task 6', 'after', 'P1 (open)',
                     'P2 (delivered)', 'Future', '#10', '#14', 'at least 12%', '7d unknown'):
            self.assertIn(part, text)
        for part in ('#7 Task 7', '#99', 'P3', 'Past', 'Finished', '95%', 'reserve'):
            self.assertNotIn(part, text)
        self.assertLess(text.index('#9 Task 9'), text.index('#8 Task 8'))
        self.assertLessEqual(len(text.splitlines()), 40)

    def test_empty_and_documents_board(self):
        self.project.update(kind='documents', isGit=False)
        text = self.build()
        self.assertEqual(text.count('- none'), 6)
        self.assertIn('no cached reading', text)

    def test_overflow_never_hides_a_category(self):
        for no in range(30):
            self.room(no + 1)
            self.room(no + 50, launched=False)
        text = self.build()
        self.assertIn('23 more', text)
        self.assertIn('26 more', text)
        self.assertIn('### Usage', text)
        self.assertLessEqual(len(text.splitlines()), 40)

    def test_due_uses_scheduler_anchor_not_new_file_mtime(self):
        self.hp.write_text('## Due\n- 14:00 — Already fired\n', encoding='utf-8')
        with mock.patch.object(d.due, '_load', return_value={'seen': {str(self.hp): {
                '- 14:00 — Already fired': {'due': self.now - 86400}}}}):
            self.assertNotIn('Already fired', self.build())

    def test_bad_source_does_not_prevent_prompt(self):
        with mock.patch.object(d.attention, 'by_room', side_effect=OSError):
            self.assertIn('unavailable', self.build())

    def test_git_counts_use_one_walk_when_supported(self):
        self.room(1)
        self.room(2, report={'kind': 'completed'})
        def git(cwd, *args, **kw):
            if args[0] == 'for-each-ref':
                return 'sess/ED-1\t3 8\nsess/ED-2\t0 10'
            self.assertNotEqual(args[0], 'rev-list')
            return ''
        with mock.patch.object(d, '_git_out', side_effect=git):
            text = self.build()
        self.assertIn('3 ahead of main', text)
        self.assertNotIn('#2 Task 2', text)

    def test_reopened_completion_is_running_with_or_without_new_commits(self):
        for branch, ahead in [('merged', '0'), ('sess/ED-1', '2')]:
            with self.subTest(ahead=ahead):
                self.rooms.clear()
                self.room(1, workflow='inprogress', workflowAt=200,
                          report={'kind': 'completed', 'ts': 100},
                          lastReport={'kind': 'update', 'ts': 201},
                          workspace={'branch': branch})
                text = self.build()
                running = text.split('### Running')[1].split('### Done')[0]
                done = text.split('### Done, not merged')[1].split('### Drafts')[0]
                self.assertIn('#1 Task 1; working', running)
                self.assertIn(f'{ahead} ahead of main', running)
                self.assertNotIn('#1 Task 1', done)

    def test_po_first_prompt_routes_preserve_tag_and_context(self):
        for cause in ('', 'manual', 'usage_limit'):
            text = rotation.first_prompt(self.project, {'id': 'po'}, 'old', 200000,
                                         cause=cause, kinds=('claude', 'codex'))
            self.assertTrue(text.startswith('[rotation] ## Board now'))
            self.assertLess(text.index('## Board now'), text.index('You are'))
            self.assertIn('ROADMAP.md', text)
            self.assertIn('PO-HANDOVER.md', text)
        for builder in (d.made_po_first_input, d.made_po_fresh_input):
            text = builder(self.project)
            self.assertTrue(text.startswith('[product owner] ## Board now'))
            self.assertEqual(d.hub_input_kind(text)['kind'], 'madepo')
        with mock.patch.object(d, 'load_projects', return_value=[self.project]):
            for note, kind in [(d.RESTART_NOTE, 'restart'),
                               ('[from the restart helper, not sam] Carry on', 'helper')]:
                text = d.po_hub_prompt({'id': 'po'}, note)
                self.assertIn('## Board now', text)
                self.assertEqual(d.classify_turns([{'role': 'user', 'text': text}])[0]['kind'], kind)
                self.assertEqual(d.po_hub_prompt({'id': 'task'}, note), note)

    def test_launch_and_seeded_resume_get_brief_once(self):
        prompts = []
        part = dict(identity='codex', agent='codex', kind='agent', sessionId='', cwd=self.tmp.name)
        room = dict(id='po', title='PO', cwd=self.tmp.name, sharedCwd=True, participants=[part])
        h = d.Handler.__new__(d.Handler)
        h.server = SimpleNamespace(server_address=('127.0.0.1', 8791))
        with (mock.patch.object(d, 'load_projects', return_value=[self.project]),
              mock.patch.object(d.Handler, '_mcp_wiring', return_value=([], [], {})),
              mock.patch.object(d, '_rtk_task_wiring', return_value=([], {}, '')),
              mock.patch.object(d.agents, 'get_agent', return_value=object()),
              mock.patch.object(d.BACKEND, 'headless_launch', side_effect=lambda cwd, argv, prompt: prompts.append(prompt) or argv),
              mock.patch.object(d.ptyrun, 'create', return_value=SimpleNamespace(id='new'))):
            h._launch_room_agent_pty(room, part, 'Original', collab=False)
            h._launch_room_agent_pty(room, part, '', prompt=d.made_po_fresh_input(self.project))
            h._resume_room_agent_pty(room, part, seed='Original')
            part['sessionId'] = 'old'
            h._resume_room_agent_pty(room, part, seed='Original')
        self.assertEqual([s.count('## Board now') for s in prompts], [1, 1, 1, 0])


class SizeWarning(_Base):
    def test_warning_not_repeated_when_the_same_rotation_retries(self):
        subject = self.subject('po')
        subject['handover'].write_bytes(b'x' * 40000)
        self.check(subject)
        subject['state'].update(phase='watching', lastAttempt=0)
        self.check(subject)
        self.assertEqual(len(self.sess.typed), 2)
        self.assertEqual(sum('over 30 KB' in s for s in self.sess.typed), 1)

    def test_size_threshold_missing_and_owner(self):
        hp = Path(self.temp.name) / 'h'
        self.assertEqual(rotation.handover_size_warning(hp), '')
        for size in (30 * 1024, 30 * 1024 + 1):
            hp.write_bytes(b'x' * size)
            text = rotation.handover_size_warning(hp)
            self.assertEqual(bool(text), size > 30 * 1024)
            if text:
                self.assertIn('30,721 bytes', text)
                self.assertIn('PO-HANDOVER-ARCHIVE.md', text)
        owner = self.subject()
        owner['handover'].write_bytes(b'x' * 40000)
        self.check(owner)
        self.assertNotIn('over 30 KB', self.sess.typed[-1])

    def test_large_po_asked_once_and_still_rotates_without_edit(self):
        subject = self.subject('po')
        subject['handover'].write_bytes(b'x' * 40000)
        self.check(subject)
        self.check(subject)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertEqual(self.sess.typed[0].count('over 30 KB'), 1)
        self.idle = True
        subject['state']['askedAt'] -= rotation.ASK_TIMEOUT_S + 1
        self.sess._last_submit = time.time() - rotation.IDLE_S - 1
        self.check(subject)
        self.assertEqual(len(self.rotated), 1)


class RestartRoutes(_Hub):
    def test_snapshot_po_first_still_builds_after_its_task_is_restored(self):
        po, task = self.room(title='PO'), self.room(title='Task')
        self.projects.append({'id': 'p', 'poRoomId': po})
        for rid in (po, task):
            d.chatroom.patch_room(rid, projectId='p')
            self.running(rid, 'claude', 'working')
        d.take_restart_snapshot(self.lease(), wake_room='')
        path = d._restart_snapshot_path()
        import json
        snap = json.loads(path.read_text(encoding='utf-8'))
        snap['hubPid'] = -1
        snap['rooms'].sort(key=lambda r: r['roomId'] != po)
        path.write_text(json.dumps(snap), encoding='utf-8')
        self.hub_stops()
        observed = []
        def brief(*args, **kw):
            observed.append(d._room_is_live(d.chatroom.get_room(task, public=False)))
            return '## Board now (from the hub, test)\n- task working'
        h = self.handler()
        with mock.patch.object(board_brief, 'build', side_effect=brief):
            h._restore_after_restart('lease-1')
            self.join()
        self.assertEqual(observed, [True])
        self.assertEqual(len(self.typed(po)['claude']), 1)
        self.assertIn('task working', self.typed(po)['claude'][0])

    def test_only_mid_turn_po_gets_a_brief(self):
        rid = self.room()
        self.projects.append({'id': 'p', 'poRoomId': rid})
        with mock.patch.object(board_brief, 'build', return_value='## Board now (test)') as brief:
            h = self.handler()
            h._start_or_resume_room(d.chatroom.get_room(rid, public=False), restart={})
            self.join()
            brief.assert_not_called()
            self.hub_stops()
            h._start_or_resume_room(d.chatroom.get_room(rid, public=False), restart={'claude': 'working'})
            self.join()
            brief.assert_called_once()


class HelperInput(unittest.TestCase):
    def test_helper_builds_after_restart_and_pastes_one_input(self):
        writes = []
        sess = SimpleNamespace(meta={'room': 'po'}, alive=lambda: True,
                               write=lambda text: writes.append(text) or True,
                               hub_line_typed=False)
        project = {'id': 'p', 'poRoomId': 'po'}
        with (mock.patch.object(d, 'load_projects', return_value=[project]),
              mock.patch.object(d.chatroom, 'get_room', return_value={'id': 'po'}),
              mock.patch.object(board_brief, 'build', return_value='## Board now (from the hub, test)\n- current') as brief):
            endpoint = test_terminal_input.PtyInputEndpointFilter()
            self.assertEqual(endpoint.post(sess, '[from the restart helper, not sam] Carry on', hub=True), b'200')
            self.assertEqual(endpoint.post(sess, '\r', hub=True), b'200')
            brief.assert_called_once()
        self.assertTrue(writes[0].startswith('\x1b[200~[from the restart helper, not sam] ## Board now'))
        self.assertTrue(writes[0].endswith('\x1b[201~'))
        self.assertEqual(writes[1], '\r')


if __name__ == '__main__':
    unittest.main()
