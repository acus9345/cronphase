import contextlib
import io
import os
import unittest
from cronphase.parser import CronExpression, parse_manifest
from cronphase.engine import simulate_day
from cronphase.solver import SolverConfig, optimize_fleet
from cronphase.diff import apply_rewrites, generate_unified_diff
from cronphase.cli import main as cli_main


class TestCronPhase(unittest.TestCase):
    def test_cron_expression_parsing_and_macros(self):
        c1 = CronExpression.parse("*/15 * * * *")
        self.assertEqual(c1.minutes, (0, 15, 30, 45))
        self.assertEqual(len(c1.firing_minutes_in_day()), 4 * 24)

        c2 = CronExpression.parse("@midnight")
        self.assertEqual(c2.minutes, (0,))
        self.assertEqual(c2.hours, (0,))
        self.assertEqual(c2.firing_minutes_in_day(), [0])

        c3 = CronExpression.parse("7-59/15 * * * *")
        self.assertEqual(c3.minutes, (7, 22, 37, 52))

    def test_dom_dow_or_semantics(self):
        c = CronExpression.parse("0 0 15 * 1")  # 15th of month OR Monday
        self.assertTrue(c.matches_minute_of_day(0, day_of_week=1, day_of_month=2))
        self.assertTrue(c.matches_minute_of_day(0, day_of_week=3, day_of_month=15))
        self.assertFalse(c.matches_minute_of_day(0, day_of_week=3, day_of_month=2))

    def test_midnight_spillover_simulation(self):
        manifest = "55 23 * * * /bin/backup.sh # @name=late-job @duration=10m @weight=5.0\n"
        jobs = parse_manifest(manifest)
        sim = simulate_day(jobs)
        self.assertEqual(sim.timeline[1435].concurrency, 1)
        self.assertEqual(sim.timeline[0].concurrency, 1)
        self.assertEqual(sim.timeline[4].concurrency, 1)
        self.assertEqual(sim.timeline[5].concurrency, 0)

    def test_phase_shift_solver_reduces_peak_and_preserves_frequency(self):
        raw = """
        */15 * * * * /bin/job_a.sh # @name=job-a @duration=3m @weight=5.0
        */15 * * * * /bin/job_b.sh # @name=job-b @duration=3m @weight=5.0
        */15 * * * * /bin/job_c.sh # @name=job-c @duration=3m @weight=5.0
        0 * * * * /bin/job_d.sh # @name=job-d @duration=4m @weight=6.0 @lock
        0 * * * * /bin/job_e.sh # @name=job-e @duration=4m @weight=6.0
        """
        jobs = parse_manifest(raw)
        opt_jobs, rewrites, before_sim, after_sim = optimize_fleet(jobs, SolverConfig(strategy="anneal"))

        self.assertAlmostEqual(before_sim.peak_load, 27.0)
        self.assertLess(after_sim.peak_load, before_sim.peak_load)
        self.assertLessEqual(after_sim.peak_concurrency, 2)

        job_d_after = [j for j in opt_jobs if j.name == "job-d"][0]
        self.assertEqual(job_d_after.cron.format_canonical(), "0 * * * *")

        for orig, opt in zip(jobs, opt_jobs):
            self.assertEqual(
                len(orig.cron.firing_minutes_in_day()),
                len(opt.cron.firing_minutes_in_day()),
            )

        rewritten = apply_rewrites(raw, jobs, rewrites)
        diff_txt = generate_unified_diff(raw, rewritten, "crontab")
        self.assertIn("--- a/crontab", diff_txt)
        self.assertIn("+++ b/crontab", diff_txt)

    def test_multi_hour_lattice_shifting(self):
        raw = """
        0 */4 * * * /bin/sync_a.sh # @name=sync-a @duration=10m @weight=7.0
        0 */4 * * * /bin/sync_b.sh # @name=sync-b @duration=10m @weight=7.0
        """
        jobs = parse_manifest(raw)
        opt_jobs, _, before_sim, after_sim = optimize_fleet(jobs)
        self.assertAlmostEqual(before_sim.peak_load, 14.0)
        self.assertAlmostEqual(after_sim.peak_load, 7.0)
        for orig, opt in zip(jobs, opt_jobs):
            self.assertEqual(len(orig.cron.firing_minutes_in_day()), len(opt.cron.firing_minutes_in_day()))

    def test_yaml_parsing_and_strategies(self):
        yaml_txt = """
apiVersion: batch/v1
kind: CronJob
metadata:
  name: k8s-rollup-a
spec:
  schedule: "*/10 * * * *" # @duration=2m @weight=4.0
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: k8s-rollup-b
spec:
  schedule: "*/10 * * * *" # @duration=2m @weight=4.0
"""
        jobs = parse_manifest(yaml_txt)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].name, "k8s-rollup-a")
        self.assertEqual(jobs[0].source_format, "k8s")

        for strat in ("fnv-hash", "prime-splay", "anneal"):
            _, _, b_sim, a_sim = optimize_fleet(jobs, SolverConfig(strategy=strat))
            self.assertLessEqual(a_sim.peak_load, b_sim.peak_load)

    def test_dow_7_matches_sunday(self):
        c = CronExpression.parse("0 0 * * sun")
        self.assertTrue(c.matches_minute_of_day(0, day_of_week=0))
        self.assertTrue(c.matches_minute_of_day(0, day_of_week=7))
        self.assertFalse(c.matches_minute_of_day(0, day_of_week=1))

    def test_single_hour_and_multi_hour_window_constraints(self):
        # Single-hour window must move daily job into hour 2 across all strategies
        for strat in ("anneal", "fnv-hash", "prime-splay"):
            jobs = parse_manifest("0 0 * * * /bin/a.sh # @window=02-02\n")
            opt, _, _, _ = optimize_fleet(jobs, SolverConfig(strategy=strat))
            self.assertEqual(opt[0].cron.hours, (2,))

        # Multi-hour */12 job with @window=02-16 must stay within hours 2..16
        for strat in ("anneal", "fnv-hash", "prime-splay"):
            jobs = parse_manifest(
                "0 */12 * * * /bin/a.sh # @name=a @window=02-16\n"
                "0 */12 * * * /bin/b.sh # @name=b @window=02-16\n"
            )
            opt, _, _, _ = optimize_fleet(jobs, SolverConfig(strategy=strat))
            for j in opt:
                self.assertTrue(all(2 <= h <= 16 for h in j.cron.hours), (strat, j.cron.hours))

        # Multi-hour */4 job cannot fit in 01-05, so hour field must not be shifted outside
        jobs = parse_manifest("0 */4 * * * /bin/a.sh # @window=01-05\n")
        opt, _, _, _ = optimize_fleet(jobs, SolverConfig(strategy="anneal"))
        self.assertEqual(opt[0].cron.hours, (0, 4, 8, 12, 16, 20))

    def test_window_24_hour_end_parsing(self):
        jobs = parse_manifest(
            "0 0 * * * /bin/a.sh # @window=00:00-24:00\n"
            "0 18 * * * /bin/b.sh # @window=18:00-24:00\n"
        )
        self.assertEqual((jobs[0].window_start_hour, jobs[0].window_end_hour), (0, 23))
        self.assertEqual((jobs[1].window_start_hour, jobs[1].window_end_hour), (18, 23))

    def test_no_noop_format_only_rewrites(self):
        jobs = parse_manifest("01 03 * * * /bin/a.sh\n")
        opt, rw, _, _ = optimize_fleet(jobs, SolverConfig(strategy="anneal", preserve_hours_for_daily=True))
        self.assertEqual(rw, [])
        self.assertEqual(opt[0].cron.raw, "01 03 * * *")

    def test_crontab_comment_isolation_and_tab_comments(self):
        raw = (
            "# Set PATH for jobs\n"
            "PATH=/usr/local/bin:/usr/bin\n"
            "0 0 * * * /bin/actual.sh\t# @name=actual-job @duration=5m\n"
        )
        jobs = parse_manifest(raw)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].name, "actual-job")
        self.assertEqual(jobs[0].duration_min, 5)
        opt, rw, _, _ = optimize_fleet(jobs)
        if rw:
            rewritten = apply_rewrites(raw, jobs, rw)
            self.assertIn("\t# @name=actual-job @duration=5m", rewritten)

    def test_yaml_metadata_clamping_and_github_actions(self):
        gh_yaml = """
name: Nightly Audit
on:
  schedule:
    - cron: '0 * * * *' # @duration=999m @weight=0.01
    - cron: '0 * * * *' # @duration=4m @weight=5.0
"""
        jobs = parse_manifest(gh_yaml)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].source_format, "github")
        self.assertEqual(jobs[0].name, "Nightly Audit")
        self.assertEqual(jobs[0].duration_min, 180)
        self.assertEqual(jobs[0].weight, 0.5)

    def test_cli_path_diff_header_and_error_handling(self):
        buf = io.StringIO()
        manifest_path = os.path.join("examples", "prod.crontab")
        with contextlib.redirect_stdout(buf):
            rc = cli_main([manifest_path, "--optimize", "--diff"])
        self.assertEqual(rc, 0)
        self.assertIn("--- a/prod.crontab", buf.getvalue())
        self.assertIn("+++ b/prod.crontab", buf.getvalue())

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_missing = cli_main(["does-not-exist-cronphase.crontab"])
        self.assertEqual(rc_missing, 1)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc_gate = cli_main(["examples/prod.crontab", "--max-crest-factor", "2.5"])
        self.assertEqual(rc_gate, 2)


if __name__ == "__main__":
    unittest.main()
