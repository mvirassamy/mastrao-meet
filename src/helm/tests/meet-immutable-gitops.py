"""Local Helm rendering only; no cluster, registry or YAML package required.

The fixture is a representative selection, not an applicable staging binding.
Its additive Django migration is PreSync, before ordinary Sync consumers. All
configuration/database dependencies must already exist. Full sync is required:
selective sync skips hooks. HookSucceeded retains a failed named Job for diagnosis;
there is no BeforeHookCreation, automatic retry or failure TTL in this fixture.
An operator must diagnose partial/non-atomic migrations before another attempt.
"""

import copy
import json
import re
import subprocess
import unittest
from pathlib import Path

HELM = Path(__file__).resolve().parents[1]
CHART = HELM / 'meet'
FIXTURE = json.loads((HELM / 'tests/fixtures/meet-immutable-gitops.json').read_text())
BACKEND = 'fixture.invalid/meet-backend@sha256:' + 'a' * 64
FRONTEND = 'fixture.invalid/meet-frontend@sha256:' + 'b' * 64
AGENTS = 'fixture.invalid/meet-agents@sha256:' + 'c' * 64
SUMMARY = 'fixture.invalid/meet-summary@sha256:' + 'd' * 64
BACKEND_DEPLOYMENTS = (
    'backend',
    'celery-backend',
    'celery-mastrao-native-admission',
    'celery-mastrao-transcription',
    'celery-mastrao-native-asr-ready',
    'celery-mastrao-native-asr-retry',
)
SUMMARY_DEPLOYMENTS = (
    'summary', 'celery-transcribe-default', 'celery-summarize', 'celery-summary-backend'
)


def render(values=None):
    return subprocess.run(
        ['helm', 'template', 'fixture-meet', str(CHART), '--namespace',
         'fixture-staging', '--values', '-'],
        input=json.dumps(values or {}), text=True, capture_output=True, check=False,
    )


def resources(manifest):
    result = {}
    for document in re.split(r'(?m)^---\s*$', manifest):
        kind = re.search(r'(?m)^kind: (\S+)$', document)
        if not kind:
            continue
        name = re.search(r'(?m)^  name: (\S+)$', document)
        key = (kind[1], name[1] if name else '<list>')
        if key in result:
            raise AssertionError(f'duplicate resource: {key}')
        result[key] = document
    return result


def images(document):
    return re.findall(r'(?m)^\s+image: "([^"]+)"$', document)


def component_values(path, value):
    values = copy.deepcopy(FIXTURE)
    parent = values
    for part in path.split('.'):
        parent = parent.setdefault(part, {})
    parent['digest'] = value
    return values


class ImmutableGitOpsTests(unittest.TestCase):
    def rendered(self, values=None):
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        return resources(result.stdout)

    def resource(self, rendered, kind, suffix):
        return rendered[(kind, 'fixture-meet-' + suffix)]

    def test_complete_selection_pins_every_container_and_preserves_metadata_command(self):
        rendered = self.rendered(FIXTURE)
        expected = {name: BACKEND for name in BACKEND_DEPLOYMENTS}
        expected.update({name: SUMMARY for name in SUMMARY_DEPLOYMENTS})
        expected.update({'frontend': FRONTEND, 'agent-metadata': AGENTS,
                         'agent-subtitles': AGENTS})
        deployments = {name.removeprefix('fixture-meet-'): images(doc)
                       for (kind, name), doc in rendered.items() if kind == 'Deployment'}
        self.assertEqual(deployments, {name: [image] for name, image in expected.items()})
        self.assertEqual(images(self.resource(rendered, 'Job', 'backend-migrate')), [BACKEND])
        cronjobs = rendered[('CronJobList', '<list>')]
        self.assertEqual(images(cronjobs), [BACKEND] * 3)
        self.assertIn('name: fixture-meet-backend-reconcile-mastrao-recordings', cronjobs)
        self.assertIn('reconcile_mastrao_recordings --limit 20', cronjobs)
        metadata = self.resource(rendered, 'Deployment', 'agent-metadata')
        self.assertIn('- metadata_collector.py\n', metadata)
        self.assertIn('- start\n', metadata)
        all_images = [image for doc in rendered.values() for image in images(doc)]
        self.assertEqual(len(all_images), 17)
        self.assertTrue(all('@sha256:' in image for image in all_images))

    def test_admin_and_explicit_data_repair_jobs_also_use_backend_digest(self):
        values = copy.deepcopy(FIXTURE)
        values['backend']['createsuperuser']['enabled'] = True
        values['backend']['mergeDuplicateUsers'] = {'enabled': True}
        rendered = self.rendered(values)
        for job in ('migrate', 'createsuperuser', 'merge-duplicate-users'):
            document = self.resource(rendered, 'Job', 'backend-' + job)
            self.assertEqual(images(document), [BACKEND])
            self.assertNotIn('ttlSecondsAfterFinished:', document)
            if job != 'migrate':
                self.assertNotIn('argocd.argoproj.io/hook:', document)

    def test_development_default_images_jobs_and_commands_stay_unchanged(self):
        rendered = self.rendered()
        expected = {name: 'lasuite/meet-backend:latest' for name in BACKEND_DEPLOYMENTS}
        expected.update({name: 'lasuite/meet-summary:latest' for name in SUMMARY_DEPLOYMENTS})
        expected.update({'frontend': 'lasuite/meet-frontend:latest',
                         'agent-metadata': 'lasuite/meet-agents:latest',
                         'agent-subtitles': 'lasuite/meet-agents:latest'})
        deployments = {name.removeprefix('fixture-meet-'): images(doc)
                       for (kind, name), doc in rendered.items() if kind == 'Deployment'}
        self.assertEqual(deployments, {name: [image] for name, image in expected.items()})
        self.assertEqual(images(rendered[('CronJobList', '<list>')]),
                         ['lasuite/meet-backend:latest'] * 3)
        jobs = {name for kind, name in rendered if kind == 'Job'}
        self.assertEqual(jobs, {'fixture-meet-backend-migrate', 'fixture-meet-backend-createsuperuser'})
        for name in jobs:
            job = rendered[('Job', name)]
            self.assertEqual(images(job), ['lasuite/meet-backend:latest'])
            self.assertIn('ttlSecondsAfterFinished: 30', job)
            self.assertIn('backoffLimit: 2', job)
            self.assertIn('Replace=true,Force=true', job)
            self.assertNotIn('activeDeadlineSeconds:', job)
            self.assertNotIn('argocd.argoproj.io/hook:', job)
        self.assertIn('python manage.py migrate --no-input',
                      self.resource(rendered, 'Job', 'backend-migrate'))

    def test_custom_development_tags_remain_supported_for_every_component(self):
        values = {'image': {'tag': 'dev-backend'}}
        components = ('frontend', 'agentMetadata', 'agentSubtitles', 'summary',
                      'celeryTranscribe', 'celerySummarize', 'celerySummaryBackend')
        for component in components:
            values[component] = {'image': {'tag': 'dev-' + component}}
        rendered = self.rendered(values)
        for name in BACKEND_DEPLOYMENTS:
            self.assertEqual(images(self.resource(rendered, 'Deployment', name)),
                             ['lasuite/meet-backend:dev-backend'])
        for name, component, repository in (
            ('frontend', 'frontend', 'meet-frontend'),
            ('agent-metadata', 'agentMetadata', 'meet-agents'),
            ('agent-subtitles', 'agentSubtitles', 'meet-agents'),
            ('summary', 'summary', 'meet-summary'),
            ('celery-transcribe-default', 'celeryTranscribe', 'meet-summary'),
            ('celery-summarize', 'celerySummarize', 'meet-summary'),
            ('celery-summary-backend', 'celerySummaryBackend', 'meet-summary'),
        ):
            self.assertEqual(images(self.resource(rendered, 'Deployment', name)),
                             [f'lasuite/{repository}:dev-{component}'])

    def test_global_digest_is_backend_only_and_component_digest_wins(self):
        rendered = self.rendered({'image': FIXTURE['image']})
        for name in ('backend', 'celery-backend', 'celery-mastrao-native-admission'):
            self.assertEqual(images(self.resource(rendered, 'Deployment', name)), [BACKEND])
        self.assertEqual(images(self.resource(rendered, 'Deployment', 'frontend')),
                         ['lasuite/meet-frontend:latest'])
        override = component_values('backend.image', 'sha256:' + 'e' * 64)
        rendered = self.rendered(override)
        for name in BACKEND_DEPLOYMENTS:
            self.assertEqual(images(self.resource(rendered, 'Deployment', name)),
                             ['fixture.invalid/meet-backend@sha256:' + 'e' * 64])

    def test_explicit_empty_component_digest_selects_tag(self):
        values = component_values('backend.image', '')
        rendered = self.rendered(values)
        for name in BACKEND_DEPLOYMENTS:
            self.assertEqual(images(self.resource(rendered, 'Deployment', name)),
                             ['fixture.invalid/meet-backend:latest'])

    def test_repository_or_tag_override_never_carries_global_backend_digest(self):
        for override, expected in (
            ({'repository': 'fixture.invalid/other-backend'}, 'fixture.invalid/other-backend:latest'),
            ({'tag': 'reviewed-tag'}, 'fixture.invalid/meet-backend:reviewed-tag'),
            ({'pullPolicy': 'Always'}, BACKEND),
        ):
            with self.subTest(override=override):
                values = copy.deepcopy(FIXTURE)
                values['backend']['image'] = override
                rendered = self.rendered(values)
                for name in BACKEND_DEPLOYMENTS:
                    self.assertEqual(images(self.resource(rendered, 'Deployment', name)), [expected])

    def test_instance_images_inherit_or_explicitly_override_component_digest(self):
        values = copy.deepcopy(FIXTURE)
        values['celeryTranscribe']['instances'] = [
            {'name': 'inherited'},
            {'name': 'pinned', 'image': {'digest': 'sha256:' + 'e' * 64}},
            {'name': 'tagged', 'image': {'digest': '', 'tag': 'reviewed'}},
            {'name': 'repository', 'image': {'repository': 'fixture.invalid/other-summary'}},
            {'name': 'tag-only', 'image': {'tag': 'other-tag'}},
        ]
        expected = {
            'inherited': SUMMARY,
            'pinned': 'fixture.invalid/meet-summary@sha256:' + 'e' * 64,
            'tagged': 'fixture.invalid/meet-summary:reviewed',
            'repository': 'fixture.invalid/other-summary:latest',
            'tag-only': 'fixture.invalid/meet-summary:other-tag',
        }
        rendered = self.rendered(values)
        for instance, image in expected.items():
            self.assertEqual(images(self.resource(rendered, 'Deployment', 'celery-transcribe-' + instance)), [image])

    def test_malformed_and_non_string_digests_are_rejected_at_every_image_input(self):
        paths = ('image', 'backend.image', 'frontend.image', 'agentSubtitles.image',
                 'agentMetadata.image', 'summary.image', 'celeryTranscribe.image',
                 'celerySummarize.image', 'celerySummaryBackend.image')
        for path in paths:
            for invalid in ('latest', 'sha256:' + 'a' * 63, 'sha256:' + 'A' * 64,
                            'sha512:' + 'a' * 64, 'sha256:' + 'g' * 64,
                            0, False, ['sha256:' + 'a' * 64], {'digest': 'invalid'}):
                with self.subTest(path=path, invalid=invalid):
                    result = render(component_values(path, invalid))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(path + '.digest must be sha256', result.stderr)
        values = copy.deepcopy(FIXTURE)
        values['celeryTranscribe']['instances'] = [{'name': 'invalid', 'image': {'digest': False}}]
        result = render(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('celeryTranscribe.instances[0].image.digest must be sha256', result.stderr)

    def test_gitops_migration_is_ordered_bounded_and_retains_failure_for_diagnosis(self):
        rendered = self.rendered(FIXTURE)
        migrate = self.resource(rendered, 'Job', 'backend-migrate')
        for annotation in ('argocd.argoproj.io/hook: PreSync',
                           'argocd.argoproj.io/sync-wave: "-1"',
                           'argocd.argoproj.io/hook-delete-policy: HookSucceeded'):
            self.assertIn(annotation, migrate)
        self.assertIn('activeDeadlineSeconds: 600', migrate)
        self.assertIn('backoffLimit: 0', migrate)
        self.assertIn('restartPolicy: Never', migrate)
        self.assertNotIn('ttlSecondsAfterFinished:', migrate)
        all_manifests = '\n'.join(rendered.values())
        for destructive in ('Force=true', 'Replace=true', 'BeforeHookCreation', 'HookFailed'):
            self.assertNotIn(destructive, all_manifests)
        self.assertEqual({name for kind, name in rendered if kind == 'Job'},
                         {'fixture-meet-backend-migrate'})
        for (kind, _), document in rendered.items():
            if kind in ('Deployment', 'CronJobList'):
                self.assertNotIn('argocd.argoproj.io/hook:', document)
                self.assertNotIn('argocd.argoproj.io/sync-wave:', document)

    def test_jobs_can_be_omitted_without_disabling_workers_or_cronjobs(self):
        values = copy.deepcopy(FIXTURE)
        values['backend']['migrate']['enabled'] = False
        rendered = self.rendered(values)
        self.assertFalse(any(kind == 'Job' for kind, _ in rendered))
        self.assertIn(('CronJobList', '<list>'), rendered)
        self.assertIn(('Deployment', 'fixture-meet-backend'), rendered)

    def test_invalid_migration_deadlines_are_rejected(self):
        for deadline in (0, -1, 1.5, True, 'forever'):
            with self.subTest(deadline=deadline):
                values = copy.deepcopy(FIXTURE)
                values['backend']['migrate']['activeDeadlineSeconds'] = deadline
                result = render(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('backend.migrate.activeDeadlineSeconds must be a positive integer', result.stderr)


if __name__ == '__main__':
    unittest.main()
