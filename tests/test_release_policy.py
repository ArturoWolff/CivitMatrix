import unittest
import importlib
import importlib.util


from typing import Any


def metadata(label, vid, name="item.safetensors", **updates) -> dict[str, Any]:
    data = dict(ModelId=1, VersionId=vid, VersionName=label,
                AuthorUsername="creator", BaseModel="Anima", ModelType="LORA",
                RemoteFileId=vid, RemoteFileName=name,
                FileMetadata={"format": "SafeTensor", "fp": "fp16", "size": "full"})
    data.update(updates)
    return data


class ReleasePolicyTests(unittest.TestCase):
    def test_reason_api_and_filename_branches(self):
        from civitmatrix.release_policy import proven_supersession_reason
        self.assertTrue(proven_supersession_reason(metadata("v1", 100), metadata("v2", 200)))
        self.assertIsNone(proven_supersession_reason(metadata("v1", 100, "alice.safetensors"),
                                                     metadata("v2", 200, "bob.safetensors")))
        self.assertTrue(proven_supersession_reason(metadata(None, 100, "alice_v1.safetensors"),
                                                   metadata(None, 200, "alice_v2.safetensors")))
        self.assertIsNone(proven_supersession_reason(metadata(None, 100, "alice.safetensors"),
                                                     metadata(None, 200, "alice.safetensors")))

    def test_publisher_training_tracks_override_matching_filenames_and_triggers(self):
        from civitmatrix.release_policy import classify_supersession, proven_supersession_reason
        # Review row 19: publisher text from cmpctrlV0.3.cm-info.json. Numbers
        # describe validation/comprehensive tracks, not universal succession.
        warning = (
            "The numbering primarily reflects different training tracks and does not "
            "strictly indicate quality or recency ranking. Taste is subjective, but "
            "within the same track, newer versions are generally preferred over older ones. "
            "Going forward, decimal versions (e.g., v1.1) are intended for validated "
            "models, and integer versions (e.g., v1) for comprehensively trained models."
        )
        old = metadata("anima-base V1", 100, "cmpctrlV1.safetensors",
                       ModelName="Composition Self-Control", TrainedWords=["cmpctrl"])
        new = metadata("anima base v1.1", 200, "cmpctrlV0.3.safetensors",
                       ModelName="Composition Self-Control", TrainedWords=["cmpctrl"],
                       ModelDescription="<p>" + warning + "</p>")
        decision = classify_supersession(old, new)
        self.assertFalse(decision.supersedes)
        self.assertEqual(decision.reason, "publisher-non-supersession-warning")
        self.assertIsNone(proven_supersession_reason(old, new))
        # Publisher warnings are not model-ID exceptions and may appear on either
        # endpoint, in either description, including HTML and Chinese sidecars.
        warnings = [warning, "版本号主要反映训练路线，不严格代表质量或新旧排序。",
                    "This is not a replacement for the old version.",
                    "V1 still has its charm.",
                    "v1 is kept for its darker, rougher look."]
        for text in warnings:
            for key in ("ModelDescription", "VersionDescription"):
                for endpoint in (0, 1):
                    with self.subTest(text=text, key=key, endpoint=endpoint):
                        pair = [metadata("v1", 100), metadata("v2", 200)]
                        pair[endpoint][key] = "<p>" + text + "</p>"
                        self.assertFalse(classify_supersession(*pair).supersedes)

    def test_optional_file_metadata_absent_and_null_are_equivalent(self):
        from civitmatrix.release_policy import classify_supersession
        old = metadata("v1", 100, FileMetadata={"format": "SafeTensor"})
        new = metadata("v2", 200, FileMetadata={"format": "SafeTensor", "fp": None, "size": None})
        self.assertTrue(classify_supersession(old, new).supersedes)
        self.assertTrue(classify_supersession(
            metadata("v1", 100, FileMetadata={"format": "safetensor"}), new).supersedes)
        for extra in ({"fp": "fp16"}, {"size": "pruned"}, {"rank": "16"}, {"rank": None}):
            with self.subTest(extra=extra):
                changed = dict(new, FileMetadata={**new["FileMetadata"], **extra})
                self.assertFalse(classify_supersession(old, changed).supersedes)
        old["FileMetadata"]["rank"] = "16"
        new["FileMetadata"]["rank"] = "16"
        self.assertTrue(classify_supersession(old, new).supersedes)

    def test_normalized_filenames_establish_lineage_without_version_on_both_files(self):
        from civitmatrix.release_policy import classify_supersession
        # Actual reviewed sidecar fields: rows 11, 38, 42, 68, 92, 101, 182,
        # 190 and 191. Trigger changes/absence cannot manufacture a version:
        # rising, compatible semantic labels are still independently required.
        cases = [
            ("Aerilune Style", "v1.0", "v2.0", "Aerilune_epoch_15", "aerilunev2-epoch-20"),
            ("Monstyle", "v0.5", "v2.0", "monstyle", "monstyleV2"),

            ("Hsin (Wuthering Waves) Anima", "v1.0", "v2",
             "Hsin Wuthering Waves Anima", "Hsin (Wuthering Waves) Anima"),
            ("Brown Dust 2 Style", "v1.0", "v2.0", "bd2style", "bd2style v2"),
            ("Mirei Tennouji", "ana-v1.0", "ana-v2.0",
             "mirei_tennouji-saijo_no_osewa_s1-ana-anime-soralz",
             "mirei_tennouji-saijo_no_osewa_s1-v2-ana-anime-soralz"),

            ("Tavern - Anima", "V1", "V2", "TavernAnima", "TavernAnimaV2"),
            ("Anima Chun Li", "Anima v2.0", "Anima v3.0", "chun-li", "chunli"),
            ("GFF Style", "v1.0", "v2.0", "gff", "GFF_v2"),
        ]
        for subject, left, right, before, after in cases:
            with self.subTest(subject=subject):
                old = metadata(left, 100, before + ".safetensors", ModelName=subject)
                new = metadata(right, 200, after + ".safetensors", ModelName=subject)
                decision = classify_supersession(old, new)
                self.assertTrue(decision.supersedes, decision.reason)
                new["VersionName"] = left
                self.assertFalse(classify_supersession(old, new).supersedes)

    def test_only_bound_filename_ids_are_bookkeeping(self):
        from civitmatrix.release_policy import classify_supersession
        old = metadata('v1', 100, 'shirt_v1_epoch_10.safetensors')
        new = metadata('v2', 200, 'shirt_v2_anima_123456_epoch_20.safetensors', RemoteFileId=123456)
        self.assertTrue(classify_supersession(old, new).supersedes)
        new['RemoteFileId'] = 456789
        self.assertFalse(classify_supersession(old, new).supersedes)
        new['VersionId'] = 123456
        self.assertTrue(classify_supersession(old, new).supersedes)

    def test_shared_subject_trigger_does_not_authorize_unknown_filename_changes(self):
        from civitmatrix.release_policy import classify_supersession
        # Local sidecars reviewed in rows 4, 5, 8, 14, 18, 23, 33, 61, 85,
        # 209, 213 and 220; no dependency on an installed library in this test.
        cases = [
            ("Rebecca [One Piece] (Anima)", "v2.0", "v3.0",
             "Rebecca__One_Piece___Anima_v2", "RebeccaOnePieceV2", "r3b3cc4"),

            ("Hooves Art Style (Anima)", "v1.0", "v2.0", "hooves-art", "@hoovani hooves-art_3374024", "@hoovani"),
            ("SligarTheTiger Style (Anima/Illustrious)", "v6.0 (Anima)", "v7.0 (Anima)",
             "_sligani_epoch_10", "@sligani (sligarthetiger)", "@sligani"),
            ("Shizuka Marikawa [Highschool of the Dead] (Anima)", "v1.0", "v2.0",
             "REDD4Z7540S0GPWXBX2X4N4PN0", "ShizukaMarikawaHighschoolOfTheDeadV2", "sh1zuk4m4r1k4w4"),
            ("永世离香 Style", "v1.0", "v1.1", "@everscent-step00005750", "@everscent 1.1-step00008750", "@everscent,"),
            ("Healthipeko style", "v1.0", "v2.0", "Healthipeko style v1 base Anima lora", "healthipeko-anima-base-epoch-05", "@Healthipeko"),
            ("Alcina Dimitrescu - Resident Evil 8", "v2- ANM (L)", "v3 - ANM (L)",
             "AlcinaDimitrescuxl-10v2-ANM9", "AlcinaDimitrescuxl-11v3-ANM9", "alcinaxl,"),
            ("Iku/ikuchan_kaoru 郁 Style - Anima", "v0.1 AB1", "v1.0 AB1",
             "iku_AnimaB_v01-000045", "iku_AnimaB_v01-1-000024", "@1ku1ku,"),
            ("Alia Atreides, (Dune/Behind the dune), LoRA, Anima | PonyXL | Illustrious XL",
             "v1.0, Anima", "v2.0, Anima", "AA_bhd_anima1", "NM_Char_BTD_Alia_A", "AA_bhd,"),
            ("artist style - 八宝備仁", "v1.0", "v2.0", "happoubijinstyle_v1", "happoubijinstyle-2_v1", "@happoubijinSTYLE"),
            ("Lola (by Bureaucracy)", "v2.0", "v3.0", "l0l4x2", "xl0l4x", "xl0l4x, 1girl, solo, blonde hair"),
        ]
        for subject, left, right, before, after, trigger in cases:
            with self.subTest(subject=subject):
                old = metadata(left, 100, before + ".safetensors", ModelName=subject, TrainedWords=[trigger])
                new = metadata(right, 200, after + ".safetensors", ModelName=subject, TrainedWords=[trigger])
                decision = classify_supersession(old, new)
                self.assertFalse(decision.supersedes, decision.reason)
                new["RemoteFileName"] = "renamed-retraining.safetensors"
                decision = classify_supersession(old, new)
                self.assertFalse(decision.supersedes, decision.reason)
                self.assertEqual(decision.reason, 'unproven-filename-branch')
                # Filename changes are not evidence on their own; neither is a
                # higher installed ID or an unchanged semantic label.
                new["VersionName"] = left
                self.assertFalse(classify_supersession(old, new).supersedes)
                new["VersionName"] = right
                new["TrainedWords"] = ["1girl", "masterpiece", "long hair"]
                self.assertFalse(classify_supersession(old, new).supersedes)

    def test_collection_and_generic_triggers_do_not_establish_subject(self):
        from civitmatrix.release_policy import classify_supersession
        for name in ("Alice / Bob Collection", "Alice and Bob Pack", "角色合集", "My Loras"):
            with self.subTest(name=name):
                old = metadata("v1", 100, "alice_v1.safetensors", ModelName=name, TrainedWords=["@artist"])
                new = metadata("v2", 200, "bob_v2.safetensors", ModelName=name, TrainedWords=["@artist"])
                self.assertFalse(classify_supersession(old, new).supersedes)
                new["RemoteFileName"] = "alice_v2.safetensors"
                self.assertFalse(classify_supersession(old, new).supersedes)
        for trigger in ("1girl", "masterpiece", "long hair", "style", "anime", "high quality"):
            old = metadata("v1", 100, "alice.safetensors", ModelName="Alice", TrainedWords=[trigger])
            new = metadata("v2", 200, "bob.safetensors", ModelName="Alice", TrainedWords=[trigger])
            self.assertFalse(classify_supersession(old, new).supersedes)
        old = metadata("v1", 100, "alice.safetensors", TrainedWords=["@artist"])
        new = metadata("v2", 200, "bob.safetensors", TrainedWords=["@artist"])
        self.assertFalse(classify_supersession(old, new).supersedes)

    def test_explicit_experiments_and_publisher_alternative_style_scopes_are_preserved(self):
        from civitmatrix.release_policy import classify_supersession
        # Review 31: same custom trigger and increasing labels, but the new
        # publisher version note does not establish an upgrade rather than a test.
        old = metadata("Anima v1.0", 100, "The_Darkness_Anima_epoch_10.safetensors",
                       ModelName="The Darkness Comic Style", TrainedWords=["d4rkn3ss4nim4"])
        new = metadata("Anima V2", 200, "The_Darkness_T1_anima_3417676_epoch_7.safetensors",
                       ModelName="The Darkness Comic Style", TrainedWords=["d4rkn3ss4nim4"],
                       VersionDescription="<p>Trying to test something</p>")
        self.assertFalse(classify_supersession(old, new).supersedes)

    def test_publisher_alternative_style_scopes_are_preserved(self):
        from civitmatrix.release_policy import classify_supersession
        old, new = metadata("v1", 100), metadata("v2", 200)
        # Review 102: publisher retains the general-purpose V1 scope, not merely
        # claiming V1.3 has better quality. Test the caveat even on equal filenames.
        for text in (
            "Even though it's V2, the aesthetic and sometimes the messiness of V1 still has its charm.",
            "V1.3 looks slightly closer to the artist style with softer shading, but it is still imperfect. "
            "V1 looks very generic and has a slightly weak influence, but it can be used as a general all-purpose style.",
            "Anima v10 (new, recommended). Anima v8 (previous version, flatter and warmer).",
        ):
            with self.subTest(text=text):
                new["ModelDescription"] = text
                new["RemoteFileName"] = old["RemoteFileName"]
                self.assertFalse(classify_supersession(old, new).supersedes)

    def test_identity_and_variant_guards_cannot_be_bypassed_by_shared_subject(self):
        from civitmatrix.release_policy import classify_supersession
        old = metadata("v1", 100, "aerilune.safetensors", ModelName="Aerilune Style",
                       TrainedWords=["aerilune"])
        new = metadata("v2", 200, "aerilune_v2.safetensors", ModelName="Aerilune Style",
                       TrainedWords=["aerilune"])
        self.assertTrue(classify_supersession(old, new).supersedes)
        for changes in (
            {"ModelId": 2}, {"AuthorUsername": "other"}, {"BaseModel": "SDXL"},
            {"ModelType": "Checkpoint"}, {"RemoteFileId": None}, {"VersionId": 100},
            {"FileMetadata": {"format": "GGUF", "fp": "fp16", "size": "full"}},
            {"FileMetadata": {"format": "SafeTensor", "fp": "fp32", "size": "full"}},
            {"FileMetadata": {"format": "SafeTensor", "fp": "fp16", "size": "pruned"}},
            {"FileMetadata": {"format": "SafeTensor", "fp": "fp16", "size": "full", "rank": 64}},
            {"FileType": "Training Data"}, {"RemoteFileType": "Model"},
            {"RemoteFileName": "retrained.gguf"}, {"VersionName": "v2 SDXL"},
            {"VersionName": "v2 2.9B"}, {"VersionName": "v2 3.8B"},
            {"VersionName": "v2-pose"}, {"VersionName": "v2-early style"},
            {"VersionName": "v2-summer outfit"}, {"VersionName": "v2 2026 era"},
            {"RemoteFileName": "retrained_anima2.9b.safetensors"},
            {"RemoteFileName": "retrained_anima3.8b.safetensors"},
            {"RemoteFileName": "retrained_8step.safetensors"},
            {"RemoteFileName": "retrained_ema.safetensors"},
            {"RemoteFileName": "retrained_Balanced.safetensors"},
            {"VersionDescription": "Trained on the unofficial Anima 2.9B fork."},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(classify_supersession(old, {**new, **changes}).supersedes)

    def test_filename_scopes_are_not_overridden_by_shared_character_trigger(self):
        from civitmatrix.release_policy import classify_supersession
        for before, after in (
            ("alice_school", "alice_bikini"), ("alice_outfit1", "alice_outfit2"),
            ("alice_2025", "alice_2026"), ("alice_early", "alice_late"),
            ("alice_dark", "alice_light"), ("alice_typeB", "alice_typeA"),
            ("alice_experiment1", "alice_experiment2"),
        ):
            with self.subTest(before=before, after=after):
                old = metadata("v1", 100, before + ".safetensors", ModelName="Alice", TrainedWords=["alice"])
                new = metadata("v2", 200, after + ".safetensors", ModelName="Alice", TrainedWords=["alice"])
                self.assertFalse(classify_supersession(old, new).supersedes)

    def test_semantic_revision_and_scope(self):
        self.assertIsNotNone(importlib.util.find_spec("civitmatrix.release_policy"), "pure policy module required")
        release_policy = importlib.import_module("civitmatrix.release_policy")
        classify = getattr(release_policy, "classify_supersession", None)
        self.assertIsNotNone(classify, "pure supersession classifier is required")
        cases = [
            ("v1.0", "v67", True), ("Anime_NSFW_v1.0", "Anime_NSFW_v2", True),
            ("v10.0 (Anima)", "v11.0 (Anima)", True),
            ("Alice", "Bob", False), ("v2", "v2", False),
            ("v3", "v2", False), ("v1-typeB", "v2", False),
            ("v2", "v3-pose", False), ("96y v.0.0.1", "basement v.0.0.4", False),
            ("Remapped Anima 2.9B", "Remapped Anima 3.8B", False),
        ]
        for old, new, expected in cases:
            with self.subTest(old=old, new=new):
                result = classify(metadata(old, 100), metadata(new, 200))
                self.assertEqual(result.supersedes, expected)
                self.assertTrue(result.reason)
        for change in [dict(BaseModel="SDXL"), dict(AuthorUsername="other"),
                       dict(ModelType="Checkpoint"), dict(RemoteFileId=None),
                       dict(FileMetadata={"format": "SafeTensor", "fp": "fp32", "size": "full"}),
                       dict(RemoteFileType="Training Data")]:
            self.assertFalse(classify(metadata("v1", 100), metadata("v2", 200, **change)).supersedes)
        self.assertFalse(classify(metadata("v1", 200), metadata("v2", 100)).supersedes)
        self.assertFalse(classify(metadata("v1", 100, "item_4step.safetensors"),
                                  metadata("v2", 200, "item_8step.safetensors")).supersedes)


if __name__ == "__main__":
    unittest.main()
