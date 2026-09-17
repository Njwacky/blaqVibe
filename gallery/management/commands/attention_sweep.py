"""Run the attention sweep: duplicates, malfunctions, the 7-day decisions.

Celery beat runs the same work hourly (``gallery.tasks.attention_sweep``) and
every 30 minutes for reminders (``gallery.tasks.attention_reminders``). This
command exists for the same reason every scheduled job here has one: a
deployment without beat — or an operator who needs the answer NOW — should not
have to wait for a clock to make the loop turn.

Everything in gallery.attention is idempotent (pair_key is UNIQUE), so running
this twice in a row opens nothing twice and decides nothing twice.

    python manage.py attention_sweep                 # full pass
    python manage.py attention_sweep --dry-run       # report only, write nothing
    python manage.py attention_sweep --only detect   # one stage
    python manage.py attention_sweep --user nolo     # one owner
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

STAGES = ('detect', 'expire', 'remind', 'erase')

class Command(BaseCommand):
    help = 'Detect duplicated/broken builds, decide the ones past 7 days, remind the rest.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=400,
                            help='Owners per detection pass (default 400).')
        parser.add_argument('--only', choices=STAGES, default=None,
                            help='Run one stage instead of the whole sweep.')
        parser.add_argument('--user', default=None,
                            help='Restrict detection to one username.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what the sweep would find; write nothing.')

    def handle(self, *args, **options):
        from gallery import attention
        from gallery.models import AttentionCase

        limit = options['limit']
        only = options['only']
        now = timezone.now()

        if options['dry_run']:
            self._dry_run(limit, now)
            return

        if not attention.enabled():
            self.stdout.write(self.style.WARNING(
                'ATTENTION_ENABLED is off — nothing to do.'))
            return

        if options['user']:
            from django.contrib.auth.models import User
            user = User.objects.filter(username=options['user']).first()
            if user is None:
                self.stdout.write(self.style.ERROR(f'no such user: {options["user"]}'))
                return
            result = attention.detect_for_user(user, now=now)
            self.stdout.write(self.style.SUCCESS(
                f'@{user.username}: {result["duplicates"]} duplicate case(s), '
                f'{result["malfunctions"]} malfunction case(s) opened'))
            return

        result = attention.sweep(now=now, limit=limit) if only is None else {}
        if only == 'detect':
            result = {'detected': attention.detect(limit=limit, now=now)}
        elif only == 'expire':
            result = {'expired': attention.expire_due(now=now)}
        elif only == 'remind':
            result = {'reminded': attention.remind_due(now=now)}
        elif only == 'erase':
            result = {'erased': attention.delete_due(now=now)}

        detected = result.get('detected') or {}
        self.stdout.write(
            f"owners swept: {detected.get('owners', 0)}  "
            f"duplicates opened: {detected.get('duplicates', 0)}  "
            f"malfunctions opened: {detected.get('malfunctions', 0)}")
        self.stdout.write(
            f"auto-decided (7 days of silence): {result.get('expired', 0)}  "
            f"reminders sent: {result.get('reminded', 0)}  "
            f"final deletes: {result.get('erased', 0)}")
        waiting = AttentionCase.objects.filter(status='open').count()
        parked = AttentionCase.objects.filter(status='decided').count()
        self.stdout.write(self.style.SUCCESS(
            f'now waiting on an owner: {waiting}   parked, awaiting FINAL DELETE: {parked}'))

    def _dry_run(self, limit, now):
        """Report what the sweep would DO. No writes, no notifications.

        "Would do" is the point: a dry run that also listed the problems which
        already have a case would report work the sweep will not perform, and an
        operator reading `15 problem(s)` before a run that opens nothing learns
        to distrust the command. So every stage counts through the same
        querysets the engine uses, and detection skips what detection skips.
        """
        from django.contrib.auth.models import User
        from gallery import attention
        from gallery.models import AttentionCase

        self.stdout.write('dry run — nothing will be written')

        due = attention.expiry_due_queryset(now=now)
        self.stdout.write(f'cases past their {attention.decision_days()}-day window, would be decided: {due.count()}')
        for case in due.select_related('user')[:20]:
            self.stdout.write(f'  #{case.pk} @{case.user.username} {case.kind}: {case.headline}')

        remindable = attention.reminder_due_queryset(now=now)
        self.stdout.write(
            f'cases due a reminder (every {attention.reminder_minutes()} min): {remindable.count()}')

        erase = attention.erase_due_queryset(now=now)
        self.stdout.write(
            f'cases whose {attention.final_delete_hours()}h silence has run out: {erase.count()}')
        for case in erase.select_related('user')[:20]:
            for candidate in case.candidates.filter(outcome='parked'):
                self.stdout.write(f'  would erase: {candidate.title} (case #{case.pk})')

        owners = attention.sweep_owners(limit=limit)
        threshold = attention.duplicate_threshold()
        found = 0
        already_open = AttentionCase.objects.filter(status='open').count()
        for row in owners:
            user = User.objects.filter(pk=row['owner']).first()
            if user is None:
                continue
            projects = list(attention._candidate_projects(user))
            # Everything the engine would refuse to open again: a pair whose
            # case already exists (including dismissed ones — one answer per
            # question), a build already inside an open case (a third copy joins
            # it rather than minting a second), and a defect already reported.
            known_keys = set(AttentionCase.objects.filter(user=user)
                             .values_list('pair_key', flat=True))
            folded = {p.pk for p in projects
                      if attention._open_case_for_project(user, 'duplicate', p)}
            for a, b, score, signals in attention.duplicate_pairs(user, threshold=threshold):
                # The engine would FOLD this pair into the case that already
                # covers one of its builds, and would refuse to reopen a pair it
                # has already asked about (dismissed included — one answer per
                # question). Either way a sweep opens nothing new here.
                key = attention.pair_key_for('duplicate', user.pk, min(a.pk, b.pk), max(a.pk, b.pk))
                if key in known_keys or a.pk in folded or b.pk in folded:
                    continue
                found += 1
                self.stdout.write(
                    f'  would open: @{user.username} “{a.title}” vs “{b.title}” '
                    f'score {score} ({", ".join(s["label"] for s in signals)})')
            for project in projects:
                problem = attention.malfunction_of(project, now=now)
                if not problem:
                    continue
                key = attention.pair_key_for('malfunction', user.pk, project.pk, problem['code'])
                if key in known_keys:
                    continue
                found += 1
                self.stdout.write(
                    f'  would open: @{user.username} “{project.title}” — {problem["code"]} '
                    f'({problem["severity"]})')
        self.stdout.write(self.style.SUCCESS(
            f'{found} new case(s) would be opened  ·  {already_open} already waiting on an owner'))
