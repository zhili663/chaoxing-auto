# UI / UX design requirements

The frontend must not be treated as a mechanical mapping from API fields to form controls.

A technically functional page is not considered well-designed merely because:

- every backend field is exposed;
- shadcn components are used;
- the layout is visually clean;
- all configuration is accessible somewhere.

For every frontend feature, explicitly reason about:

- what job the screen performs;
- what information deserves primary visibility;
- what information is secondary;
- what actions are frequent;
- what actions are rare;
- what actions are dangerous;
- what information belongs in overview context;
- what belongs in item detail context;
- what deserves a modal/sheet/sub-view;
- which interaction control best matches the actual data semantics;
- how much information a user should see at one time;
- how the workflow behaves on desktop and mobile.

The goal is an interface that is ergonomic, information-dense where useful, progressively disclosed where complexity exists, and visually calm without becoming functionally minimal.

Avoid both extremes:

```
everything visible at once
```

and:

```
extreme minimalism that hides useful state or requires excessive navigation
```

---

# shadcn skill usage

When working on frontend architecture or implementation, use the official shadcn skill if available.

If the project does not already have it installed, the implementation task may install it using the current official mechanism:

```
pnpm dlx skills add shadcn/ui
```

Before designing or implementing a non-trivial interface:

1. inspect the project's `components.json`;
2. inspect the project's existing component style and primitive backend;
3. use the shadcn skill / CLI documentation lookup rather than relying on stale memory;
4. use `shadcn search`, `shadcn docs`, registry search or equivalent tools where useful;
5. inspect existing shadcn Blocks for relevant application patterns before inventing a large custom layout;
6. reuse appropriate project components where they already exist.

Do NOT change the project's Base UI / Radix choice merely because a different primitive implementation is newer.

Do not create a custom primitive when an appropriate accessible shadcn primitive already exists unless the custom behavior provides clear product value.

The shadcn skill helps with component APIs and composition.

It does NOT replace UX reasoning.

---

# UI responsibility analysis

Before specifying any substantial page or modal, determine its responsibility.

For each view, explicitly state:

```
Primary job
Secondary jobs
Primary entities
Primary actions
Secondary actions
Risky/destructive actions
Information required for scanning
Information required only during editing
Information required only during diagnosis
```

A page should have a coherent operational purpose.

Do not place a control somewhere merely because there is available visual space.

Do not append new settings to an existing page only because that page is vaguely related.

Ask:

```
Where would a user naturally look for this?

What object does this action operate on?

At what stage of the workflow is this information useful?

Does showing it permanently improve decision-making,
or does it only increase cognitive load?
```

The resulting layout should follow these answers.

---

# Information hierarchy

Use explicit visual and interaction hierarchy.

Distinguish:

1. overview information;
2. operational state;
3. frequently used actions;
4. detailed configuration;
5. diagnostics;
6. advanced configuration;
7. destructive actions.

Do not give all seven equal visual weight.

Examples:

A Provider list should primarily expose enough information to answer:

```
Which providers exist?
Are they healthy?
Which models/routes use them?
Is one disabled or failing?
```

It should NOT render every timeout, endpoint option, retry setting, credential field and model parameter directly in every list row.

An Account list should primarily answer:

```
Which accounts exist?
Which platform?
Are they connected?
What role/control mode applies?
Is intervention required?
```

Detailed session information belongs deeper.

---

# Progressive disclosure

Prefer progressive disclosure for objects with significant configuration.

A common pattern should be:

```
Overview page
    ↓
select item
    ↓
large detail Dialog / Sheet / route-backed detail view
    ↓
internal sections / tabs
    ↓
small subordinate action Dialog/Popover when necessary
```

Do not assume every entity deserves a dedicated top-level page.

Do not assume every configuration option belongs directly in the list page.

For multi-item resources such as:

- Providers
- Platform Accounts
- Roles
- Model Routes
- Skills
- Automations
- Contacts
- Agent instances

the primary page should usually optimize for:

```
scan
compare
filter
search
identify state
perform common actions
```

Detailed configuration can open into a substantial secondary surface.

---

# Detail surface selection

Choose the interaction surface intentionally.

## Dialog

Prefer a large Dialog when:

- the user is editing one bounded entity;
- preserving the surrounding list context is useful;
- the edit can reasonably be completed without navigating elsewhere;
- the operation temporarily deserves full attention.

A complex Dialog may contain:

- sticky header;
- scrollable body;
- sticky action footer;
- internal navigation;
- Tabs;
- grouped configuration sections.

A Dialog is not limited to tiny confirmation forms.

## Sheet

Prefer a Sheet when:

- the information supplements the currently visible page;
- preserving visual awareness of the underlying list/context is valuable;
- the user is inspecting or lightly editing an item;
- the interaction feels like a contextual side panel rather than a separate workflow.

## Dedicated route/page

Prefer a dedicated page when:

- the workflow is deep;
- the state should be bookmarkable/shareable;
- the user may spend significant time there;
- several independent subdomains exist;
- navigation history matters;
- the surface itself becomes a workbench.

## Drawer

Consider Drawer particularly for mobile adaptation.

A desktop Dialog or Sheet may become a Drawer on narrow/touch-oriented layouts when appropriate.

## Popover

Use for lightweight contextual interaction:

- quick selection;
- small filter;
- command picker;
- compact metadata;
- secondary control.

Do not put a full configuration workflow inside a small Popover.

---

# Nested detail interactions

Secondary and tertiary interaction layers are allowed.

Do not artificially flatten every workflow.

For example:

```
Providers page
  └── Provider Detail Dialog
        ├── Overview
        ├── Models
        ├── Credentials
        ├── Reliability
        └── Model row
              └── Model Configuration Dialog
```

or:

```
Role Detail Dialog
  ├── Identity
  ├── Model
  ├── Style
  ├── Context
  └── Skills
        └── Skill Detail Dialog
```

However, nested modal depth must have semantic justification.

A third-level modal is acceptable for a small atomic subtask such as:

- editing one model;
- rotating one credential;
- inspecting one delivery attempt;
- confirming a destructive action;
- configuring one nested item.

Do not create endless modal recursion.

If the subordinate workflow itself becomes substantial, promote it to a stronger navigation surface.

---

# Modal ergonomics

Large configuration Dialogs/Sheets must be designed as proper work surfaces rather than oversized forms.

Consider:

- logical internal navigation;
- scroll containment;
- sticky headers/footers;
- save/cancel location;
- unsaved-change protection;
- meaningful section titles;
- responsive dimensions;
- keyboard navigation;
- focus restoration;
- destructive action separation;
- dirty state;
- loading state;
- partial save/error state.

Do not create a 30-field vertical form when the fields naturally divide into several conceptual sections.

---

# Component selection must follow data semantics

Do NOT default to `<Input type="text">`.

Before choosing a control, inspect:

- domain type;
- valid range;
- number of possible values;
- whether precision matters;
- whether the effect is immediate;
- whether multiple values may be selected;
- whether users know the possible values;
- frequency of editing.

Use components semantically.

## Free-form short text

Use:

```
Input
```

Examples:

- display name
- endpoint URL
- username

## Long-form text

Use:

```
Textarea
```

Examples:

- system prompt
- description
- instruction template

For large prompts/code/configuration, consider an appropriately styled editor rather than a tiny textarea.

## Boolean setting

Use `Switch` when the semantic meaning is:

```
enabled / disabled
on / off
```

especially when it represents a persistent setting.

Use `Checkbox` when the semantic meaning is:

```
selected / included / acknowledged
```

especially in forms or multi-selection.

Do not mechanically substitute one for the other.

## Small mutually-exclusive enum

For approximately 2–5 visually meaningful choices, strongly consider:

```
ToggleGroup
```

as a segmented-control-like interaction.

Examples:

```
Agent | Human | Hybrid | Paused

Low | Medium | High

Ordered | Weighted
```

Do not use a Select for every enum.

## Larger enum

Use:

```
Select
```

when users choose one known value from a moderate option set.

Use:

```
Combobox / Command + Popover
```

when:

- the option set is large;
- search is useful;
- values are dynamically loaded.

## Numeric precise value

Use a numeric input.

Do not store/display an integer or float through a generic text input when numeric semantics are known.

Provide:

- min/max;
- step;
- unit;
- validation.

## Numeric bounded preference

Strongly consider:

```
Slider
```

when users reason about a relative position in a meaningful bounded range.

Examples may include:

- temperature;
- creativity;
- threshold;
- percentage;
- confidence cutoff.

When precise values are also useful, combine:

```
Slider + numeric input
```

rather than forcing the user to choose between ergonomics and precision.

shadcn Slider supports controlled values and bounded ranges; use this rather than manually recreating range interactions.

## Secret

Use a password/secret-oriented control.

Consider:

```
InputGroup
+ masked Input
+ reveal action
+ replace/update action
```

Never display persisted secret values as ordinary editable text.

## Multi-value selection

Use the appropriate combination of:

```
Command
Popover
Checkbox
Badge
```

or an existing project multi-select implementation.

Do not ask users to type comma-separated identifiers unless the domain explicitly calls for raw text input.

## Date/time/duration

Use controls that express their semantic type.

Do not require users to manually type an arbitrary string when the value has structured temporal meaning.

## Destructive actions

Use explicit destructive styling and confirmation where consequences justify it.

Use `AlertDialog` or equivalent confirmation semantics rather than an ordinary generic form Dialog.

---

# Pair controls when one control alone is insufficient

Good UX does not require every value to have exactly one control.

Examples:

```
Slider
+
numeric value

search input
+
filter chips

model picker
+
provider/status metadata

secret field
+
reveal/copy/replace action

duration number
+
unit selector
```

Use `InputGroup`, `ButtonGroup` and compositional shadcn primitives where appropriate instead of scattering unrelated controls.

---

# Forms

Use the project's shadcn Field patterns.

Prefer semantic composition such as:

```
FieldSet
  FieldLegend
  FieldDescription

  FieldGroup
    Field
      FieldLabel
      Control
      FieldDescription
      FieldError
```

Group fields according to user concepts, not database table structure.

Backend entity structure does not dictate frontend form structure.

Do not produce:

```
one card per database column
```

or:

```
one generic Input per JSON property
```

when the domain has richer meaning.

---

# Lists and collections

Choose list representation based on user task.

Use a Data Table when users primarily need to:

- compare entities across common attributes;
- sort;
- filter;
- scan many rows;
- perform bulk actions.

Use richer cards/list rows when:

- each entity has heterogeneous status;
- visual identity matters;
- actions/status do not fit naturally into columns;
- item count is moderate.

Do not automatically turn every collection into cards.

Do not automatically turn every collection into a table.

List rows/cards should expose the most useful summary, not every property.

Clicking an item should normally provide a clear path to deeper inspection/configuration.

---

# Action placement

Place actions near the object or decision they affect.

Distinguish:

```
primary
secondary
contextual
destructive
bulk
```

actions.

Avoid large toolbars containing unrelated controls.

Avoid putting rare administrative actions next to the most frequently used action with equal visual prominence.

Use overflow menus for genuinely secondary item actions.

Do not hide frequently used actions behind an overflow menu solely for visual minimalism.

---

# Settings ergonomics

Do not treat Settings as a dumping ground.

Configuration should live at the narrowest meaningful scope.

Examples:

```
Provider-specific configuration
→ Provider detail

Account-specific configuration
→ Account detail

Role behavior
→ Role detail

Workspace-wide defaults
→ Settings

Security policy
→ appropriate security/admin surface
```

Only put something into global Settings when it is actually global.

---

# Status and observability in UI

Operational products need visible state.

Where relevant, represent states such as:

- connecting;
- connected;
- degraded;
- retrying;
- disabled;
- failed;
- pending;
- processing;
- stale;
- human-controlled;
- agent-controlled.

Do not require the operator to infer state from missing data.

Use semantic Badge/status indicators, descriptions and timestamps where appropriate.

Avoid excessive decorative badges with no operational value.

---

# Error/loading/empty states

Every substantial feature spec must account for:

- initial loading;
- background revalidation;
- empty state;
- partial data;
- permission denied;
- API failure;
- disconnected platform;
- stale state;
- optimistic mutation;
- mutation failure;
- retry.

Do not design only the successful populated screenshot.

Prefer Skeleton or stable layout during initial load where appropriate rather than causing layout jumps.

Empty states should suggest the next useful action when one exists.

---

# Responsive ergonomics

Do not implement responsiveness as:

```
desktop UI, but narrower
```

For each substantial surface evaluate:

- desktop information density;
- narrow viewport behavior;
- mobile/touch interaction;
- dialog → drawer adaptation;
- table → compact list adaptation where necessary;
- navigation collapse;
- action reachability;
- touch target size;
- scroll ownership.

Preserve functionality without forcing desktop density into a phone viewport.

---

# Accessibility

Maintain shadcn's accessible primitives and semantics.

Ensure:

- every form control has a meaningful label;
- keyboard navigation works;
- focus transitions make sense;
- Dialog/Drawer focus behavior is correct;
- validation is announced appropriately;
- destructive actions are explicit;
- color is not the only carrier of state;
- icon-only controls have accessible names.

Do not remove accessibility semantics merely to simplify styling.

---

# Visual density

This is an operational application, not a marketing website.

Prefer:

- restrained spacing;
- clear grouping;
- consistent density;
- useful metadata;
- strong hierarchy;
- compact controls where frequency warrants it.

Avoid:

- giant whitespace with very little information;
- oversized headings on every settings screen;
- excessive card nesting;
- card-inside-card-inside-card layouts;
- unnecessary gradients/decorative effects;
- turning every section into a bordered container;
- arbitrary visual separators.

Use whitespace to explain hierarchy, not to reduce information density.

---

# Avoid card soup

Do not mechanically wrap every conceptual section in a `Card`.

A Card should represent an actual grouped surface or entity.

Prefer:

- typography;
- FieldSet;
- separators;
- Tabs;
- aligned sections;
- table/list structure;

when these communicate hierarchy more naturally.

---

# Avoid form soup

Likewise, do not solve configuration pages by rendering:

```
Label
Input

Label
Input

Label
Input

Label
Input
...
```

for every property.

Before implementing a configuration surface, categorize fields into:

```
identity
behavior
operational state
connection
security
advanced configuration
diagnostics
```

or the appropriate domain-specific grouping.

Then select controls and disclosure depth accordingly.

---

# UX flow before JSX

For every significant frontend roadmap item/spec, include a short UX model before describing implementation.

Use approximately this structure:

```
Surface:
Providers

Primary responsibility:
Monitor and manage configured model providers.

Overview:
- provider identity
- enabled state
- health
- models
- current usage

Primary actions:
- add provider
- open provider
- enable/disable

Secondary actions:
- test connection
- duplicate
- delete

Detail interaction:
Large Dialog.

Internal detail sections:
- General
- Credentials
- Models
- Routing/Health

Nested interactions:
Model configuration opens a subordinate Dialog.

Mobile:
Detail Dialog becomes Drawer or full-screen surface.
```

Do this before specifying individual React components.

---

# Component rationale

For non-trivial configuration UI, the feature spec should identify the intended shadcn components and briefly explain the semantic choice.

For example:

```
Temperature:
Slider + numeric Input because it is bounded but users may require exact values.

Control mode:
single ToggleGroup because there are four mutually exclusive,
high-frequency states.

Provider:
Combobox because provider/model sets may grow and need search.

Enabled:
Switch because it represents persistent binary state.

Advanced provider details:
secondary section inside the Provider detail Dialog,
not permanently visible on the list page.
```

The purpose is not documentation verbosity.

The purpose is to force intentional component selection.

If the entire proposed form consists almost exclusively of generic text Inputs, re-evaluate the design before implementation.

---

# Existing patterns and Blocks

Before creating a substantial dashboard/list/settings surface from scratch:

1. inspect available shadcn Blocks and registry patterns;
2. identify reusable structural ideas;
3. adapt them to the product's information architecture;
4. do not blindly copy a Block if its interaction model does not fit.

Blocks may be used as implementation references for:

- sidebar shells;
- dashboards;
- authentication;
- tables;
- settings structures;
- application layouts.

The product's domain requirements remain authoritative.

---

# Product coherence

New features must feel like they belong to the same application.

Before designing a new feature:

- inspect existing comparable screens;
- reuse interaction conventions;
- reuse dimensions/density;
- reuse detail-surface patterns;
- reuse status vocabulary;
- reuse button hierarchy;
- reuse form patterns.

Do not create a new UX language for every feature.

If an existing pattern is poor, improve it deliberately and propagate the improved convention where justified rather than silently diverging.

---

# UX acceptance criteria

For every roadmap feature with substantial frontend work, include UX acceptance criteria in addition to technical Definition of Done.

At minimum ask:

1. Can the user understand the primary purpose of the screen without reading documentation?
2. Is the most frequent action easy to reach?
3. Are secondary details hidden until useful without becoming hard to discover?
4. Do controls match the semantic type of the data?
5. Does the page avoid unnecessary text inputs/selects?
6. Are complex entities given an appropriate detail surface?
7. Is the information hierarchy obvious?
8. Are destructive and privileged actions visually/interactionally distinct?
9. Are loading/error/empty states usable?
10. Does the workflow remain practical on a narrow viewport?
11. Can an operator efficiently use the UI repeatedly, not merely complete it once?
12. Does the implementation avoid both visual clutter and excessive minimalism?

If the answer to these is not clear from the spec, the UI portion of the feature is not sufficiently designed.

---

# Roadmap planning requirement

When producing the remaining product roadmap, do not write frontend requirements such as:

```
Add provider page.
Add role settings.
Add memory UI.
```

Instead, each substantial feature spec must define:

```
information architecture
screen responsibility
overview vs detail information
navigation/disclosure hierarchy
detail surface choice
field/control semantics
primary/secondary/destructive actions
realtime states
loading/error/empty states
responsive behavior
expected shadcn primitives/patterns
UX acceptance criteria
```

The future implementation Agent should not have to invent the entire UX architecture while simultaneously writing JSX.