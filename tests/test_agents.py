import json
import unittest

from frutlups.agents import (
    AGENT_MODES,
    REQUIRED_AGENT_ROLES,
    AgentMode,
    AgentProfile,
    AgentRole,
    RoleAssignment,
    RoleConfig,
    default_role_config,
    local_role_config,
    required_agent_roles,
    role_config_from_assignments,
    role_config_preset,
    role_config_presets,
    same_family_role_config,
    swapped_role_config,
    validate_agent_profile,
    validate_role_config,
)


def _full_config(**families: str) -> RoleConfig:
    """Build a complete four-role config; per-role family overridable."""
    return RoleConfig(
        assignments=(
            RoleAssignment(
                AgentRole.ARCHITECT,
                AgentProfile(label="architect", family=families.get("architect", "gpt")),
            ),
            RoleAssignment(
                AgentRole.REVIEWER,
                AgentProfile(label="reviewer", family=families.get("reviewer", "gpt")),
            ),
            RoleAssignment(
                AgentRole.CODER,
                AgentProfile(label="coder", family=families.get("coder", "anthropic")),
            ),
            RoleAssignment(
                AgentRole.HUMAN,
                AgentProfile(label="human", family=families.get("human", "human")),
            ),
        )
    )


class AgentRoleTests(unittest.TestCase):
    def test_default_role_config_is_a_preset_not_a_requirement(self) -> None:
        config = default_role_config()

        self.assertEqual(config.profile_for(AgentRole.ARCHITECT).family, "gpt")
        self.assertEqual(config.profile_for(AgentRole.REVIEWER).family, "gpt")
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "anthropic")

    def test_roles_can_share_or_swap_families(self) -> None:
        same_family = AgentProfile(label="one family", family="gpt")
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.ARCHITECT, same_family),
                RoleAssignment(AgentRole.REVIEWER, same_family),
                RoleAssignment(AgentRole.CODER, same_family),
            )
        )

        self.assertEqual(config.profile_for(AgentRole.CODER), same_family)
        self.assertIsNone(config.profile_for(AgentRole.HUMAN))


# ---------------------------------------------------------------------------
# Required role set
# ---------------------------------------------------------------------------

class RequiredRolesTests(unittest.TestCase):
    def test_required_roles_are_exactly_the_four_core_roles(self) -> None:
        self.assertEqual(
            set(required_agent_roles()),
            {AgentRole.ARCHITECT, AgentRole.REVIEWER, AgentRole.CODER, AgentRole.HUMAN},
        )

    def test_required_roles_order_is_stable(self) -> None:
        self.assertEqual(
            required_agent_roles(),
            (AgentRole.ARCHITECT, AgentRole.REVIEWER, AgentRole.CODER, AgentRole.HUMAN),
        )
        self.assertEqual(required_agent_roles(), REQUIRED_AGENT_ROLES)

    def test_agent_role_enum_has_exactly_four_members(self) -> None:
        self.assertEqual(
            [r.value for r in AgentRole],
            ["architect", "reviewer", "coder", "human"],
        )


# ---------------------------------------------------------------------------
# Default preset covers all four roles, but is not a hard requirement
# ---------------------------------------------------------------------------

class DefaultPresetTests(unittest.TestCase):
    def test_default_config_includes_all_four_roles(self) -> None:
        config = default_role_config()
        self.assertEqual(config.assigned_roles(), required_agent_roles())
        self.assertEqual(config.missing_roles(), ())
        self.assertEqual(validate_role_config(config), ())

    def test_default_architect_and_reviewer_share_one_profile(self) -> None:
        config = default_role_config()
        self.assertIs(
            config.profile_for(AgentRole.ARCHITECT),
            config.profile_for(AgentRole.REVIEWER),
        )

    def test_human_is_a_manual_local_role(self) -> None:
        config = default_role_config()
        human = config.profile_for(AgentRole.HUMAN)
        self.assertEqual(human.family, "human")

    def test_default_is_not_required_other_configs_validate(self) -> None:
        # A config not using GPT/Anthropic at all is still valid.
        config = _full_config(
            architect="local", reviewer="local", coder="local", human="human"
        )
        self.assertEqual(validate_role_config(config), ())


# ---------------------------------------------------------------------------
# Swapping and same-family assignment
# ---------------------------------------------------------------------------

class SwapAndShareTests(unittest.TestCase):
    def test_coder_swapped_to_same_family_as_architect_reviewer(self) -> None:
        shared = AgentProfile(label="one gpt", family="gpt")
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.ARCHITECT, shared),
                RoleAssignment(AgentRole.REVIEWER, shared),
                RoleAssignment(AgentRole.CODER, shared),
                RoleAssignment(AgentRole.HUMAN, AgentProfile(label="human", family="human")),
            )
        )
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "gpt")
        self.assertEqual(validate_role_config(config), ())

    def test_roles_can_be_swapped_across_families(self) -> None:
        config = _full_config(architect="anthropic", coder="gpt")
        self.assertEqual(config.profile_for(AgentRole.ARCHITECT).family, "anthropic")
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "gpt")
        self.assertEqual(validate_role_config(config), ())


# ---------------------------------------------------------------------------
# Missing / duplicate diagnostics
# ---------------------------------------------------------------------------

class DiagnosticsTests(unittest.TestCase):
    def test_missing_roles_surfaced_in_order(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.CODER, AgentProfile(label="c")),
            )
        )
        self.assertEqual(
            config.missing_roles(),
            (AgentRole.ARCHITECT, AgentRole.REVIEWER, AgentRole.HUMAN),
        )
        errors = validate_role_config(config)
        self.assertIn("missing required role: architect", errors)
        self.assertIn("missing required role: human", errors)

    def test_duplicate_roles_surfaced(self) -> None:
        prof = AgentProfile(label="p")
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.ARCHITECT, prof),
                RoleAssignment(AgentRole.REVIEWER, prof),
                RoleAssignment(AgentRole.CODER, prof),
                RoleAssignment(AgentRole.CODER, prof),
                RoleAssignment(AgentRole.HUMAN, prof),
            )
        )
        self.assertEqual(config.duplicate_roles(), (AgentRole.CODER,))
        self.assertIn(
            "duplicate assignment for role: coder", validate_role_config(config)
        )

    def test_profile_for_returns_first_on_duplicate(self) -> None:
        first = AgentProfile(label="first")
        second = AgentProfile(label="second")
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.CODER, first),
                RoleAssignment(AgentRole.CODER, second),
            )
        )
        self.assertIs(config.profile_for(AgentRole.CODER), first)

    def test_assigned_roles_stable_order(self) -> None:
        # Construct out of required order; assigned_roles normalises it.
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.HUMAN, AgentProfile(label="h")),
                RoleAssignment(AgentRole.CODER, AgentProfile(label="c")),
                RoleAssignment(AgentRole.ARCHITECT, AgentProfile(label="a")),
                RoleAssignment(AgentRole.REVIEWER, AgentProfile(label="r")),
            )
        )
        self.assertEqual(config.assigned_roles(), required_agent_roles())


# ---------------------------------------------------------------------------
# Non-raising validation for malformed constructible inputs
# ---------------------------------------------------------------------------

class MalformedInputTests(unittest.TestCase):
    def test_non_role_config_input(self) -> None:
        self.assertEqual(
            validate_role_config("nope"),  # type: ignore[arg-type]
            ("config must be a RoleConfig instance",),
        )

    def test_non_assignment_entry_does_not_raise(self) -> None:
        config = RoleConfig(assignments=(123,))  # type: ignore[arg-type]
        errors = validate_role_config(config)
        self.assertTrue(any("must be a RoleAssignment" in e for e in errors))
        # Inspection helpers also tolerate the malformed entry.
        self.assertEqual(config.assigned_roles(), ())
        self.assertEqual(config.duplicate_roles(), ())

    def test_to_dict_skips_malformed_entries(self) -> None:
        config = RoleConfig(assignments=(123,))  # type: ignore[arg-type]
        self.assertEqual(config.to_dict(), {"assignments": []})


# ---------------------------------------------------------------------------
# Corrective (review 054): to_dict must not raise on malformed RoleAssignment
# fields, while validation still reports them. Policy: malformed role/profile
# fields are serialized as JSON-safe plain-Python placeholders.
# ---------------------------------------------------------------------------

class MalformedAssignmentFieldToDictTests(unittest.TestCase):
    def test_non_agentrole_role_does_not_raise(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment("coder", AgentProfile(label="c")),  # type: ignore[arg-type]
            )
        )
        d = config.to_dict()  # must not raise
        json.dumps(d)
        self.assertEqual(d["assignments"][0]["role"], "coder")
        self.assertEqual(d["assignments"][0]["profile"]["label"], "c")
        self.assertIsNone(d["assignments"][0]["profile"]["family"])

    def test_non_agentprofile_profile_does_not_raise(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.CODER, "not-profile"),  # type: ignore[arg-type]
            )
        )
        d = config.to_dict()  # must not raise
        json.dumps(d)
        self.assertEqual(d["assignments"][0]["role"], "coder")
        self.assertEqual(d["assignments"][0]["profile"], "not-profile")

    def test_non_scalar_malformed_fields_are_json_safe(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment(object(), object()),  # type: ignore[arg-type]
            )
        )
        d = config.to_dict()  # must not raise
        # repr placeholders are strings -> JSON-serializable
        json.dumps(d)
        self.assertIsInstance(d["assignments"][0]["role"], str)
        self.assertIsInstance(d["assignments"][0]["profile"], str)

    def test_validation_still_reports_malformed_fields(self) -> None:
        bad_role = RoleConfig(
            assignments=(
                RoleAssignment("coder", AgentProfile(label="c")),  # type: ignore[arg-type]
            )
        )
        self.assertIn(
            "assignments[0].role must be an AgentRole",
            validate_role_config(bad_role),
        )
        bad_profile = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.CODER, "not-profile"),  # type: ignore[arg-type]
            )
        )
        self.assertIn(
            "assignments[0].profile must be an AgentProfile",
            validate_role_config(bad_profile),
        )

    def test_valid_assignment_to_dict_core_fields(self) -> None:
        a = RoleAssignment(AgentRole.CODER, AgentProfile(label="c", family="anthropic"))
        d = a.to_dict()
        self.assertEqual(d["role"], "coder")
        self.assertEqual(d["profile"]["label"], "c")
        self.assertEqual(d["profile"]["family"], "anthropic")
        self.assertIsNone(d["profile"]["model"])

    def test_valid_config_to_dict_unchanged(self) -> None:
        d = default_role_config().to_dict()
        json.dumps(d)
        self.assertEqual(len(d["assignments"]), 4)
        self.assertEqual(d["assignments"][2]["role"], "coder")
        self.assertEqual(d["assignments"][2]["profile"]["family"], "anthropic")


# ---------------------------------------------------------------------------
# role_config_from_assignments
# ---------------------------------------------------------------------------

class FromAssignmentsTests(unittest.TestCase):
    def test_from_mapping_preserves_order(self) -> None:
        config = role_config_from_assignments(
            {
                AgentRole.ARCHITECT: AgentProfile(label="a"),
                AgentRole.REVIEWER: AgentProfile(label="r"),
                AgentRole.CODER: AgentProfile(label="c"),
                AgentRole.HUMAN: AgentProfile(label="h"),
            }
        )
        self.assertEqual(config.assigned_roles(), required_agent_roles())
        self.assertEqual(validate_role_config(config), ())

    def test_from_iterable_of_assignments(self) -> None:
        config = role_config_from_assignments(
            [
                RoleAssignment(AgentRole.CODER, AgentProfile(label="c")),
            ]
        )
        self.assertEqual(config.assigned_roles(), (AgentRole.CODER,))


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class SerializationTests(unittest.TestCase):
    def test_config_to_dict_json_safe(self) -> None:
        d = default_role_config().to_dict()
        json.dumps(d)
        self.assertEqual(len(d["assignments"]), 4)

    def test_profile_and_assignment_to_dict_plain(self) -> None:
        a = RoleAssignment(AgentRole.CODER, AgentProfile(label="c", family="anthropic"))
        d = a.to_dict()
        json.dumps(d)
        self.assertEqual(d["role"], "coder")
        self.assertEqual(d["profile"]["family"], "anthropic")


# ---------------------------------------------------------------------------
# M012-S02: provider/model profile schema
# ---------------------------------------------------------------------------

class ProfileSchemaTests(unittest.TestCase):
    def test_schema_fields_to_dict_json_safe(self) -> None:
        profile = AgentProfile(
            label="gpt coder",
            family="gpt",
            provider="openai",
            model="gpt-x",
            mode="api",
            capabilities=("code", "review"),
            notes=("example only",),
        )
        d = profile.to_dict()
        json.dumps(d)
        self.assertEqual(
            set(d),
            {"label", "family", "provider", "model", "mode", "capabilities", "notes"},
        )
        self.assertEqual(d["provider"], "openai")
        self.assertEqual(d["mode"], "api")
        self.assertEqual(d["capabilities"], ["code", "review"])
        self.assertIsInstance(d["capabilities"], list)

    def test_optional_fields_default_to_none_and_empty(self) -> None:
        d = AgentProfile(label="x").to_dict()
        self.assertIsNone(d["family"])
        self.assertIsNone(d["provider"])
        self.assertIsNone(d["model"])
        self.assertIsNone(d["mode"])
        self.assertEqual(d["capabilities"], [])
        self.assertEqual(d["notes"], [])

    def test_agent_modes_constant(self) -> None:
        self.assertEqual(AGENT_MODES, ("api", "local", "manual"))
        self.assertEqual(
            [m.value for m in AgentMode], ["api", "local", "manual"]
        )

    def test_future_family_is_representable(self) -> None:
        profile = AgentProfile(label="future", family="some-future-family", mode="local")
        self.assertEqual(validate_agent_profile(profile), ())


class ProfileValidationTests(unittest.TestCase):
    def test_valid_profile_no_errors(self) -> None:
        self.assertEqual(
            validate_agent_profile(AgentProfile(label="ok", family="gpt", mode="api")),
            (),
        )

    def test_empty_label(self) -> None:
        errs = validate_agent_profile(AgentProfile(label="  "))
        self.assertTrue(any("label" in e for e in errs))

    def test_blank_family_provider_model(self) -> None:
        for field_name in ("family", "provider", "model"):
            errs = validate_agent_profile(AgentProfile(label="x", **{field_name: ""}))
            self.assertTrue(any(field_name in e for e in errs), field_name)

    def test_unknown_mode(self) -> None:
        errs = validate_agent_profile(AgentProfile(label="x", mode="rpc"))
        self.assertTrue(any("mode" in e for e in errs))

    def test_malformed_capabilities(self) -> None:
        errs = validate_agent_profile(
            AgentProfile(label="x", capabilities=("ok", ""))
        )
        self.assertTrue(any("capabilities" in e for e in errs))

    def test_non_profile_input(self) -> None:
        self.assertEqual(
            validate_agent_profile("nope"),  # type: ignore[arg-type]
            ("profile must be an AgentProfile instance",),
        )

    def test_role_config_validation_surfaces_profile_errors(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment(AgentRole.CODER, AgentProfile(label="x", mode="rpc")),
            )
        )
        errs = validate_role_config(config)
        self.assertTrue(any("assignments[0].profile: mode" in e for e in errs))


# ---------------------------------------------------------------------------
# Corrective (review 056): AgentProfile.to_dict() must stay JSON-safe for
# malformed capabilities/notes (non-string entries and non-sequence values).
# Policy: malformed values become JSON-safe plain-Python placeholders.
# ---------------------------------------------------------------------------

class ProfileToDictJsonSafetyTests(unittest.TestCase):
    def test_non_string_capability_entry_json_safe(self) -> None:
        p = AgentProfile(label="bad caps", capabilities=(object(),))  # type: ignore[arg-type]
        d = p.to_dict()  # must not raise
        json.dumps(d)
        self.assertIsInstance(d["capabilities"], list)
        self.assertIsInstance(d["capabilities"][0], str)

    def test_non_string_note_entry_json_safe(self) -> None:
        p = AgentProfile(label="bad notes", notes=(object(),))  # type: ignore[arg-type]
        d = p.to_dict()  # must not raise
        json.dumps(d)
        self.assertIsInstance(d["notes"], list)
        self.assertIsInstance(d["notes"][0], str)

    def test_non_sequence_capabilities_json_safe(self) -> None:
        p = AgentProfile(label="bad caps field", capabilities=object())  # type: ignore[arg-type]
        d = p.to_dict()  # must not raise
        json.dumps(d)

    def test_non_sequence_notes_json_safe(self) -> None:
        p = AgentProfile(label="bad notes field", notes=object())  # type: ignore[arg-type]
        d = p.to_dict()  # must not raise
        json.dumps(d)

    def test_validation_still_reports_malformed_collections(self) -> None:
        self.assertTrue(
            any("capabilities" in e
                for e in validate_agent_profile(
                    AgentProfile(label="x", capabilities=(object(),))  # type: ignore[arg-type]
                ))
        )
        self.assertTrue(
            any("notes" in e
                for e in validate_agent_profile(
                    AgentProfile(label="x", notes=object())  # type: ignore[arg-type]
                ))
        )

    def test_role_config_to_dict_json_safe_with_malformed_profile(self) -> None:
        config = RoleConfig(
            assignments=(
                RoleAssignment(
                    AgentRole.CODER,
                    AgentProfile(label="bad", capabilities=(object(),)),  # type: ignore[arg-type]
                ),
            )
        )
        json.dumps(config.to_dict())  # must not raise

    def test_valid_capabilities_serialize_unchanged(self) -> None:
        p = AgentProfile(label="ok", capabilities=("code", "review"), notes=("n",))
        d = p.to_dict()
        self.assertEqual(d["capabilities"], ["code", "review"])
        self.assertEqual(d["notes"], ["n"])

    def test_presets_serialize_unchanged(self) -> None:
        for preset in role_config_presets():
            json.dumps(preset.to_dict())
            for a in preset.config.to_dict()["assignments"]:
                self.assertEqual(a["profile"]["capabilities"], [])
                self.assertEqual(a["profile"]["notes"], [])


# ---------------------------------------------------------------------------
# M012-S02: presets (examples, not requirements)
# ---------------------------------------------------------------------------

class PresetTests(unittest.TestCase):
    def test_common_preset_validates_and_is_an_example(self) -> None:
        config = default_role_config()
        self.assertEqual(validate_role_config(config), ())
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "anthropic")

    def test_all_local_preset_has_no_gpt_anthropic(self) -> None:
        config = local_role_config()
        self.assertEqual(validate_role_config(config), ())
        families = {
            config.profile_for(r).family for r in (AgentRole.ARCHITECT, AgentRole.REVIEWER, AgentRole.CODER)
        }
        self.assertNotIn("gpt", families)
        self.assertNotIn("anthropic", families)

    def test_same_family_preset_validates(self) -> None:
        config = same_family_role_config("gpt")
        self.assertEqual(validate_role_config(config), ())
        self.assertEqual(config.profile_for(AgentRole.ARCHITECT).family, "gpt")
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "gpt")

    def test_same_family_accepts_future_family(self) -> None:
        config = same_family_role_config("future-family")
        self.assertEqual(validate_role_config(config), ())

    def test_swapped_preset_inverts_families(self) -> None:
        config = swapped_role_config()
        self.assertEqual(validate_role_config(config), ())
        self.assertEqual(config.profile_for(AgentRole.ARCHITECT).family, "anthropic")
        self.assertEqual(config.profile_for(AgentRole.CODER).family, "gpt")

    def test_preset_registry_stable_order_and_names(self) -> None:
        names = [p.name for p in role_config_presets()]
        self.assertEqual(
            names,
            [
                "common_gpt_anthropic",
                "all_local_manual",
                "same_family",
                "swapped_gpt_anthropic",
            ],
        )
        # deterministic across calls
        self.assertEqual([p.name for p in role_config_presets()], names)

    def test_every_preset_validates(self) -> None:
        for preset in role_config_presets():
            self.assertEqual(validate_role_config(preset.config), (), preset.name)

    def test_preset_lookup_by_name(self) -> None:
        self.assertIsNotNone(role_config_preset("all_local_manual"))
        self.assertIsNone(role_config_preset("does-not-exist"))

    def test_preset_to_dict_json_safe(self) -> None:
        for preset in role_config_presets():
            d = preset.to_dict()
            json.dumps(d)
            self.assertEqual(set(d), {"name", "description", "config"})

    def test_human_is_manual_mode_in_presets(self) -> None:
        for preset in role_config_presets():
            human = preset.config.profile_for(AgentRole.HUMAN)
            self.assertEqual(human.mode, "manual")


if __name__ == "__main__":
    unittest.main()
