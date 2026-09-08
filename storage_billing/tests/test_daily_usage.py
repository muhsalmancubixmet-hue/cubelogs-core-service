from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from django.test import TestCase

from core.models import Organization, OrgSettings
from storage_billing.models import StorageFile, StorageDailyUsage
from storage_billing.services import StorageService, StorageCalculationService
from subscribers.models import GlobalBillingSettings


class StorageDailyUsageTestCase(TestCase):
    def setUp(self):
        self.org_settings = OrgSettings.objects.create()
        self.org = Organization.objects.create(name="Delta Dynamics", settings=self.org_settings)

        # Set canonical billing settings
        self.g_settings = GlobalBillingSettings.get_settings()
        self.g_settings.storage_credit_size_bytes = 1_000_000_000  # 1 GB
        self.g_settings.storage_credit_monthly_price = Decimal("20.00")
        self.g_settings.storage_billing_enabled = True
        self.g_settings.save()

    def test_rule_1_upload_and_delete_same_day_counts_once(self):
        """
        Rule 8: Upload Monday, delete Monday => Monday counts once.
        """
        monday = date(2026, 9, 7)
        upload_time = datetime(2026, 9, 7, 9, 0, 0, tzinfo=dt_timezone.utc)
        delete_time = datetime(2026, 9, 7, 17, 0, 0, tzinfo=dt_timezone.utc)

        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-1",
            original_filename="daily_report.pdf",
            size_bytes=1_000_000_000,  # 1 GB
            file_path="project_attachments/2026/09/daily_report.pdf",
            uploaded_at=upload_time
        )
        StorageService.record_file_deletion(file_obj, deleted_at=delete_time)

        # File is billable on Monday
        self.assertTrue(file_obj.is_billable_on_date(monday))

        usage, created = StorageService.calculate_or_update_daily_usage(self.org, monday)
        self.assertEqual(usage.billable_bytes, 1_000_000_000)
        self.assertEqual(usage.storage_credits, 1)

    def test_rule_2_upload_monday_delete_wednesday_counts_mon_tue_wed(self):
        """
        Rule 8: Upload Monday, delete Wednesday => Mon, Tue, Wed all count.
        """
        monday = date(2026, 9, 7)
        tuesday = date(2026, 9, 8)
        wednesday = date(2026, 9, 9)
        thursday = date(2026, 9, 10)

        upload_time = datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        delete_time = datetime(2026, 9, 9, 15, 0, 0, tzinfo=dt_timezone.utc)

        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-2",
            original_filename="dataset.tar.gz",
            size_bytes=2_000_000_000,  # 2 GB
            file_path="project_attachments/2026/09/dataset.tar.gz",
            uploaded_at=upload_time
        )
        StorageService.record_file_deletion(file_obj, deleted_at=delete_time)

        # Mon, Tue, Wed count
        self.assertTrue(file_obj.is_billable_on_date(monday))
        self.assertTrue(file_obj.is_billable_on_date(tuesday))
        self.assertTrue(file_obj.is_billable_on_date(wednesday))

        # Thursday does NOT count
        self.assertFalse(file_obj.is_billable_on_date(thursday))

        # Check usages
        usage_mon, _ = StorageService.calculate_or_update_daily_usage(self.org, monday)
        self.assertEqual(usage_mon.storage_credits, 2)

        usage_tue, _ = StorageService.calculate_or_update_daily_usage(self.org, tuesday)
        self.assertEqual(usage_tue.storage_credits, 2)

        usage_wed, _ = StorageService.calculate_or_update_daily_usage(self.org, wednesday)
        self.assertEqual(usage_wed.storage_credits, 2)

        usage_thu, _ = StorageService.calculate_or_update_daily_usage(self.org, thursday)
        self.assertEqual(usage_thu.storage_credits, 0)
        self.assertEqual(usage_thu.billable_bytes, 0)
        self.assertEqual(usage_thu.storage_charge, Decimal("0.00"))

    def test_rule_3_no_charge_after_deletion_date(self):
        """
        After deletion date, file does not count.
        """
        delete_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt_timezone.utc)
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-3",
            original_filename="obsolete.log",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/obsolete.log",
            uploaded_at=datetime(2026, 8, 20, 12, 0, 0, tzinfo=dt_timezone.utc)
        )
        StorageService.record_file_deletion(file_obj, deleted_at=delete_time)

        post_delete_date = date(2026, 9, 2)
        self.assertFalse(file_obj.is_billable_on_date(post_delete_date))

        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, post_delete_date)
        self.assertEqual(usage.billable_bytes, 0)
        self.assertEqual(usage.storage_credits, 0)

    def test_rule_4_active_file_continues_to_count(self):
        """
        Active file with deleted_at = None counts on all subsequent dates.
        """
        file_obj = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-4",
            original_filename="permanent_assets.iso",
            size_bytes=3_000_000_000,  # 3 GB
            file_path="project_attachments/2026/09/permanent_assets.iso",
            uploaded_at=datetime(2026, 9, 1, 8, 0, 0, tzinfo=dt_timezone.utc)
        )
        for day in range(1, 11):
            target = date(2026, 9, day)
            self.assertTrue(file_obj.is_billable_on_date(target))
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, target)
            self.assertEqual(usage.storage_credits, 3)

    def test_rule_5_sequential_same_day_files_are_cumulative(self):
        """
        Rule 9: Sum size_bytes of EVERY distinct billable file present at ANY point during that day.
        Example:
            5 GB morning deleted
            5 GB afternoon deleted
            5 GB evening active
            => Daily billable usage = 15 GB (15 credits).
        """
        test_date = date(2026, 9, 7)

        # 5 GB morning deleted
        f1 = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-5a",
            original_filename="morning.mov",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/morning.mov",
            uploaded_at=datetime(2026, 9, 7, 8, 0, 0, tzinfo=dt_timezone.utc)
        )
        StorageService.record_file_deletion(
            f1, deleted_at=datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        )

        # 5 GB afternoon deleted
        f2 = StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-5b",
            original_filename="afternoon.mov",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/afternoon.mov",
            uploaded_at=datetime(2026, 9, 7, 12, 0, 0, tzinfo=dt_timezone.utc)
        )
        StorageService.record_file_deletion(
            f2, deleted_at=datetime(2026, 9, 7, 14, 0, 0, tzinfo=dt_timezone.utc)
        )

        # 5 GB evening kept active
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-5c",
            original_filename="evening.mov",
            size_bytes=5_000_000_000,
            file_path="project_attachments/2026/09/evening.mov",
            uploaded_at=datetime(2026, 9, 7, 18, 0, 0, tzinfo=dt_timezone.utc)
        )

        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, test_date)
        self.assertEqual(usage.billable_bytes, 15_000_000_000)
        self.assertEqual(usage.billable_gb, Decimal("15.0000"))
        self.assertEqual(usage.storage_credits, 15)

    def test_rule_6_aggregation_occurs_before_rounding(self):
        """
        Rule 10:
        0.4 GB + 0.4 GB = 0.8 GB => 1 credit, NOT 2 credits.
        """
        test_date = date(2026, 9, 7)

        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-6a",
            original_filename="part1.bin",
            size_bytes=400_000_000,  # 0.4 GB
            file_path="project_attachments/2026/09/part1.bin",
            uploaded_at=datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        )
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-6b",
            original_filename="part2.bin",
            size_bytes=400_000_000,  # 0.4 GB
            file_path="project_attachments/2026/09/part2.bin",
            uploaded_at=datetime(2026, 9, 7, 11, 0, 0, tzinfo=dt_timezone.utc)
        )

        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, test_date)
        self.assertEqual(usage.billable_bytes, 800_000_000)
        self.assertEqual(usage.billable_gb, Decimal("0.8000"))
        self.assertEqual(usage.storage_credits, 1)  # ceil(0.8) = 1

    def test_rule_7_and_8_boundary_credits(self):
        """
        1.1 GB total => 2 credits.
        0 bytes => 0 credits.
        """
        # 1.1 GB
        test_date = date(2026, 9, 7)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-7",
            original_filename="file_1_1gb.dat",
            size_bytes=1_100_000_000,  # 1.1 GB
            file_path="project_attachments/2026/09/file_1_1gb.dat",
            uploaded_at=datetime(2026, 9, 7, 5, 0, 0, tzinfo=dt_timezone.utc)
        )
        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, test_date)
        self.assertEqual(usage.storage_credits, 2)

        # 0 bytes on another date
        empty_date = date(2026, 8, 1)
        usage_empty, _ = StorageService.calculate_or_update_daily_usage(self.org, empty_date)
        self.assertEqual(usage_empty.storage_credits, 0)
        self.assertEqual(usage_empty.storage_charge, Decimal("0.00"))

    def test_month_rate_non_leap_february_28_days(self):
        """
        February 28-day non-leap month: 1 credit @ ₹20/mo => ₹20 / 28 = 0.714285714286/day (12 decimals).
        """
        d_feb28 = date(2025, 2, 15)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-feb28",
            original_filename="feb28.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2025/02/feb28.dat",
            uploaded_at=datetime(2025, 2, 15, 1, 0, 0, tzinfo=dt_timezone.utc)
        )
        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d_feb28)
        self.assertEqual(usage.days_in_month, 28)
        self.assertEqual(usage.storage_credits, 1)
        self.assertEqual(usage.storage_credit_size_bytes_snapshot, 1_000_000_000)
        self.assertEqual(usage.daily_credit_rate_snapshot, Decimal("0.714285714286"))
        self.assertEqual(usage.storage_charge, Decimal("0.714285714286"))

    def test_month_rate_leap_february_29_days(self):
        """
        February 29-day leap year month: 1 credit @ ₹20/mo => ₹20 / 29 = 0.689655172414/day (12 decimals).
        """
        d_feb29 = date(2024, 2, 15)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-feb29",
            original_filename="feb29.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2024/02/feb29.dat",
            uploaded_at=datetime(2024, 2, 15, 1, 0, 0, tzinfo=dt_timezone.utc)
        )
        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d_feb29)
        self.assertEqual(usage.days_in_month, 29)
        self.assertEqual(usage.storage_credits, 1)
        self.assertEqual(usage.storage_credit_size_bytes_snapshot, 1_000_000_000)
        self.assertEqual(usage.daily_credit_rate_snapshot, Decimal("0.689655172414"))
        self.assertEqual(usage.storage_charge, Decimal("0.689655172414"))

    def test_month_rate_30_day_month(self):
        """
        30-day month (September): 1 credit @ ₹20/mo => ₹20 / 30 = 0.666666666667/day (12 decimals).
        """
        d_sep30 = date(2026, 9, 15)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-sep30",
            original_filename="sep30.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/sep30.dat",
            uploaded_at=datetime(2026, 9, 15, 1, 0, 0, tzinfo=dt_timezone.utc)
        )
        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d_sep30)
        self.assertEqual(usage.days_in_month, 30)
        self.assertEqual(usage.storage_credits, 1)
        self.assertEqual(usage.storage_credit_size_bytes_snapshot, 1_000_000_000)
        self.assertEqual(usage.daily_credit_rate_snapshot, Decimal("0.666666666667"))
        self.assertEqual(usage.storage_charge, Decimal("0.666666666667"))

    def test_month_rate_31_day_month(self):
        """
        31-day month (August): 1 credit @ ₹20/mo => ₹20 / 31 = 0.645161290323/day (12 decimals).
        Does NOT round to 0.65 during daily metering.
        """
        d_aug31 = date(2026, 8, 15)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-aug31",
            original_filename="aug31.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/08/aug31.dat",
            uploaded_at=datetime(2026, 8, 15, 1, 0, 0, tzinfo=dt_timezone.utc)
        )
        usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d_aug31)
        self.assertEqual(usage.days_in_month, 31)
        self.assertEqual(usage.storage_credits, 1)
        self.assertEqual(usage.storage_credit_size_bytes_snapshot, 1_000_000_000)
        self.assertEqual(usage.daily_credit_rate_snapshot, Decimal("0.645161290323"))
        self.assertEqual(usage.storage_charge, Decimal("0.645161290323"))
        self.assertNotEqual(usage.storage_charge, Decimal("0.65"))

    def test_idempotent_recalculation(self):
        """
        Running calculation twice on the same organization and date must update
        the existing record deterministically, not create duplicate rows.
        """
        test_date = date(2026, 9, 7)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-idem-1",
            original_filename="test1.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/test1.dat",
            uploaded_at=datetime(2026, 9, 7, 2, 0, 0, tzinfo=dt_timezone.utc)
        )

        u1, created1 = StorageService.calculate_or_update_daily_usage(self.org, test_date)
        self.assertTrue(created1)
        self.assertEqual(u1.storage_credits, 1)

        # Upload a second file on that same day
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-idem-2",
            original_filename="test2.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/test2.dat",
            uploaded_at=datetime(2026, 9, 7, 3, 0, 0, tzinfo=dt_timezone.utc)
        )

        # Recompute
        u2, created2 = StorageService.calculate_or_update_daily_usage(
            self.org, test_date, force_recompute=True
        )
        self.assertFalse(created2)
        self.assertEqual(u1.id, u2.id)
        self.assertEqual(u2.storage_credits, 2)
        self.assertEqual(StorageDailyUsage.objects.filter(organization=self.org, usage_date=test_date).count(), 1)

    def test_31_day_month_sum_precision_regression(self):
        """
        Hardening Issue #1 & Section 3:
        31-day month (August 2026): 1 credit active all 31 days.
        Sum of unrounded daily storage charges = exactly ₹20 before/at final monthly currency quantization.
        Guarantees no overbilling drift (e.g. ₹0.65 * 31 = ₹20.15).
        """
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-aug-sum",
            original_filename="august_dataset.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/08/august_dataset.dat",
            uploaded_at=datetime(2026, 8, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

        daily_charges = []
        for day in range(1, 32):
            d = date(2026, 8, day)
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d)
            self.assertEqual(usage.storage_credits, 1)
            self.assertEqual(usage.days_in_month, 31)
            self.assertNotEqual(usage.storage_charge, Decimal("0.65"))  # Must NOT round prematurely to cents
            daily_charges.append(usage.storage_charge)

        sum_unrounded = sum(daily_charges)
        from decimal import ROUND_HALF_UP
        monthly_total = sum_unrounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(monthly_total, Decimal("20.00"))

    def test_30_day_month_sum_precision_regression(self):
        """
        30-day month (September 2026): 1 credit active all 30 days.
        Sum of unrounded daily storage charges = exactly ₹20 at monthly quantization.
        """
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-sep-sum",
            original_filename="sept_dataset.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/09/sept_dataset.dat",
            uploaded_at=datetime(2026, 9, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

        daily_charges = []
        for day in range(1, 31):
            d = date(2026, 9, day)
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d)
            self.assertEqual(usage.storage_credits, 1)
            self.assertEqual(usage.days_in_month, 30)
            daily_charges.append(usage.storage_charge)

        from decimal import ROUND_HALF_UP
        monthly_total = sum(daily_charges).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(monthly_total, Decimal("20.00"))

    def test_february_28_day_sum_precision_regression(self):
        """
        28-day February 2025: 1 credit active all 28 days.
        Sum of unrounded daily storage charges = exactly ₹20 at monthly quantization.
        """
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-feb28-sum",
            original_filename="feb25_dataset.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2025/02/feb25_dataset.dat",
            uploaded_at=datetime(2025, 2, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

        daily_charges = []
        for day in range(1, 29):
            d = date(2025, 2, day)
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d)
            self.assertEqual(usage.storage_credits, 1)
            self.assertEqual(usage.days_in_month, 28)
            daily_charges.append(usage.storage_charge)

        from decimal import ROUND_HALF_UP
        monthly_total = sum(daily_charges).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(monthly_total, Decimal("20.00"))

    def test_february_29_day_leap_sum_precision_regression(self):
        """
        29-day Leap February 2024: 1 credit active all 29 days.
        Sum of unrounded daily storage charges = exactly ₹20 at monthly quantization.
        """
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-feb29-sum",
            original_filename="feb24_dataset.dat",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2024/02/feb24_dataset.dat",
            uploaded_at=datetime(2024, 2, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

        daily_charges = []
        for day in range(1, 30):
            d = date(2024, 2, day)
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d)
            self.assertEqual(usage.storage_credits, 1)
            self.assertEqual(usage.days_in_month, 29)
            daily_charges.append(usage.storage_charge)

        from decimal import ROUND_HALF_UP
        monthly_total = sum(daily_charges).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(monthly_total, Decimal("20.00"))

    def test_custom_credit_size_calculations(self):
        """
        Hardening V2 & Section 3:
        billable_gb MUST ALWAYS mean commercial GB actually consumed:
        billable_gb = billable_bytes / 1,000,000,000.
        Changing credit size must NEVER alter actual GB usage displayed/stored.

        Case A: credit size = 1 GB, 1.1 GB usage => billable_gb = 1.1000, 2 credits
        Case B: credit size = 2 GB, 1.1 GB usage => billable_gb = 1.1000, 1 credit
        Case C: credit size = 500 MB, 1.1 GB usage => billable_gb = 1.1000, 3 credits
        Case D: credit size = 0 => safe validation failure
        Case E: negative credit size => safe validation failure
        """
        usage_bytes = 1_100_000_000  # 1.1 GB

        # Case A: credit size = 1 GB
        gb_a, cred_a = StorageCalculationService.calculate_storage_credits(usage_bytes, 1_000_000_000)
        self.assertEqual(cred_a, 2)
        self.assertEqual(gb_a, Decimal("1.1000"))

        # Case B: credit size = 2 GB (2,000,000,000 bytes)
        gb_b, cred_b = StorageCalculationService.calculate_storage_credits(usage_bytes, 2_000_000_000)
        self.assertEqual(cred_b, 1)
        self.assertEqual(gb_b, Decimal("1.1000"))  # Unchanged!

        # Case C: credit size = 500 MB (500,000,000 bytes)
        gb_c, cred_c = StorageCalculationService.calculate_storage_credits(usage_bytes, 500_000_000)
        self.assertEqual(cred_c, 3)
        self.assertEqual(gb_c, Decimal("1.1000"))  # Unchanged!

        # Case D: credit size = 0 => safe validation failure (ValueError)
        with self.assertRaises(ValueError):
            StorageCalculationService.calculate_storage_credits(usage_bytes, 0)

        # Case E: negative credit size => safe validation failure (ValueError)
        with self.assertRaises(ValueError):
            StorageCalculationService.calculate_storage_credits(usage_bytes, -1000)

        # 4-decimal commercial GB quantization check (1,123,456,789 bytes => 1.1235 GB)
        gb_quant, _ = StorageCalculationService.calculate_storage_credits(1_123_456_789, 1_000_000_000)
        self.assertEqual(gb_quant, Decimal("1.1235"))

    def test_high_credit_scale_precision_regression(self):
        """
        Hardening V2 & V3 Section 5 & 7:
        High-scale financial precision regression:
        monthly_credit_price = ₹20.00
        storage_credits = 100,000 credits (100 TB)
        31-day month (August 2026)
        Expected monthly mathematical amount: ₹2,000,000.00
        Direct calculation per day: (100000 * 20) / 31 = 64516.129032258065 (12 decimals).
        Sum of stored daily 12-decimal high-precision charges for all 31 days,
        then final monthly cent quantization, equals exactly ₹2,000,000.00.
        """
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-high-scale",
            original_filename="large_data_warehouse.img",
            size_bytes=100_000_000_000_000,  # 100,000 GB
            file_path="project_attachments/2026/08/large_data_warehouse.img",
            uploaded_at=datetime(2026, 8, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

        daily_charges = []
        for day in range(1, 32):
            d = date(2026, 8, day)
            usage, _ = StorageService.calculate_or_update_daily_usage(self.org, d)
            self.assertEqual(usage.storage_credits, 100_000)
            self.assertEqual(usage.billable_gb, Decimal("100000.0000"))
            self.assertEqual(usage.daily_credit_rate_snapshot, Decimal("0.645161290323"))
            self.assertEqual(usage.storage_charge, Decimal("64516.129032258065"))
            self.assertNotEqual(usage.storage_charge, Decimal("64516.129032300000"))
            daily_charges.append(usage.storage_charge)

        from decimal import ROUND_HALF_UP
        monthly_total = sum(daily_charges).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(monthly_total, Decimal("2000000.00"))

    def test_direct_daily_charge_vs_rounded_rate_snapshot(self):
        """
        Hardening V3 & Section 5 & 7:
        Prove storage_charge derives directly from (credits * monthly_price) / days
        and NOT from credits * rounded_daily_rate_snapshot.
        For 7 credits, ₹10/month, 31-day month:
        raw_daily_rate = 10 / 31 = 0.32258064516129...
        daily_rate_snapshot = 0.322580645161
        credits * daily_rate_snapshot = 7 * 0.322580645161 = 2.258064516127
        Direct calculation:
        (7 * 10) / 31 = 70 / 31 = 2.2580645161290322...
        storage_charge = 2.258064516129 (12 decimals)
        """
        charge = StorageCalculationService.calculate_daily_charge(7, Decimal("10.00"), 31)
        rounded_rate = StorageCalculationService.calculate_daily_credit_rate(Decimal("10.00"), 31)
        self.assertEqual(rounded_rate, Decimal("0.322580645161"))
        self.assertEqual(charge, Decimal("2.258064516129"))
        self.assertNotEqual(charge, Decimal("2.258064516127"))
        self.assertNotEqual(charge, 7 * rounded_rate)

    def test_finalized_day_protection(self):
        """
        Hardening Issue #7 & Section 13:
        - Finalized daily record remains unchanged on normal recalculation.
        - Non-finalized record can update.
        - Backoffice price changes never rewrite historical finalized daily snapshots.
        """
        past_date = date(2026, 1, 15)
        StorageService.record_file_upload(
            organization=self.org,
            source_object_id="att-daily-finalized",
            original_filename="january_archive.tar",
            size_bytes=1_000_000_000,
            file_path="project_attachments/2026/01/january_archive.tar",
            uploaded_at=datetime(2026, 1, 15, 6, 0, 0, tzinfo=dt_timezone.utc)
        )

        # Calculate and finalize the usage record
        usage, _ = StorageService.calculate_or_update_daily_usage(
            self.org, past_date, is_finalized=True
        )
        self.assertTrue(usage.is_finalized)
        original_price_snapshot = usage.monthly_credit_price_snapshot
        original_rate_snapshot = usage.daily_credit_rate_snapshot
        original_charge = usage.storage_charge
        self.assertEqual(original_price_snapshot, Decimal("20.00"))

        # Now simulate Backoffice price hike: ₹20 -> ₹100
        self.g_settings.storage_credit_monthly_price = Decimal("100.00")
        self.g_settings.save()

        # Normal recalculation on finalized day must NOT overwrite historical charge
        usage_recalc, created = StorageService.calculate_or_update_daily_usage(self.org, past_date)
        self.assertFalse(created)
        self.assertEqual(usage_recalc.monthly_credit_price_snapshot, original_price_snapshot)
        self.assertEqual(usage_recalc.daily_credit_rate_snapshot, original_rate_snapshot)
        self.assertEqual(usage_recalc.storage_charge, original_charge)
        self.assertEqual(usage_recalc.monthly_credit_price_snapshot, Decimal("20.00"))
