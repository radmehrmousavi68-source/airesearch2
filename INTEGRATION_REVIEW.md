# Integration Review: LocalChat Agent + Website Component Kit 0.4.0

## Scope reviewed

- Supplied `local_chat` agent archive.
- Supplied `website_component_kit_v0.4.0` archive.
- Python source, agent capability adapter, Django assembler, browser UI, component manifests, recipes, localization helpers, Django adapter, and tests.

## Test results

- Website Component Kit: **31 passed**.
- LocalChat agent: **34 passed, 3 failed, 9 subtests passed**.
  - Two failures are stale assertions that expect only the legacy `product_card` and `product_grid` results. WCK 0.4.0 correctly returns additional matching components.
  - One end-to-end Django workflow failure is environmental: Django is not installed in the Python interpreter used for the test run. It must be installed or the test environment must provision it before the end-to-end test can be meaningful.

## Confirmed integration gaps

1. **Recipe API is not wired into the agent workflow.** WCK 0.4.0 provides 13 page recipes, including Persian-first storefront and medical storefront recipes. The agent still selects a small hard-coded set of components in `_choose_components()` and does not use `recipe_catalog()`, `compose_recipe()`, or `assemble_recipe()`.
2. **Persian localization is not applied to generated site content.** The agent's `_default_config()` contains English labels, hero copy, sample products, navigation, FAQ, newsletter and footer text. The current generated website can therefore have Persian UI around an English website.
3. **The default theme is not Persian-first.** When no theme is selected, the assembler prefers `theme.botanical` instead of a Persian theme such as `theme.persian-modern`.
4. **Locale and direction are not persisted through composition.** The WCK API can provide `fa-IR`, `rtl`, localized numbers/currency and Jalali formatting, but the agent does not currently pass a locale choice through the build request, generated Django settings/context processor, base template, and composition record.
5. **Component discovery tests are coupled to an old catalog snapshot.** Assertions need to test stable behavior (metadata-only discovery, framework compatibility, dependency closure) rather than an obsolete exact list of product components.
6. **Django test dependency is implicit.** The agent's end-to-end tests require Django in the base interpreter, but the repository's test setup does not provision or clearly declare that prerequisite.
7. **The UI shell is already partly Persian, but the activity vocabulary and build result/status messaging remain mixed-language.** User-facing progress, errors, validation, theme names and generated starter content need a consistent Persian presentation layer; internal identifiers, paths, Python symbols and command output should remain unchanged.

## Safe integration direction

- Keep WCK as an independent package at repository root in `website_component_kit/`; keep the agent at `local_chat/`. This matches the existing adapter's expected sibling layout and avoids copying kit templates/assets into agent code.
- Add one compact agent-facing recipe discovery/inspection/assembly path. Do not load every manifest, template, CSS or JS file into model context.
- Add an explicit `locale` field (default `fa-IR`) to the build request and persisted project composition.
- Select a compatible Persian recipe and theme by default for Persian requests; allow explicit English/LTR selection.
- Build component configuration from recipe section contexts and component schemas, validate it, then assemble deterministically. Keep backend contracts explicit; do not imply that presentation templates implement payment, authentication, orders or persistence.
- Generate `lang` and `dir` attributes, include WCK base and RTL CSS in the correct order, and register the WCK Django context processor.
- Localize all generated navigation, sample data, calls to action, prices and helper text. Keep stored numeric values unlocalized; localize only presentation.
- Update tests to cover Persian storefront and medical recipes, RTL output, English/LTR opt-in, dependency resolution, overwrite protection, and the compact-context contract.
- Provision Django explicitly in the test environment and keep a separate test that verifies a clear diagnostic when Django is absent.

## Safety / scope notes

No existing user project should be overwritten. The WCK adapter's overwrite protection should remain enabled. The generated site must distinguish presentation-only starter interactions from real backend features. Payment, user accounts, order persistence, inventory and support workflows require separately implemented and tested Django backend logic.

## Branch

Review branch: `integration/agent-wck-fa`

This report records findings from the supplied archives. It is not a claim that the full source archives or the implementation changes have already been merged into the default branch.
