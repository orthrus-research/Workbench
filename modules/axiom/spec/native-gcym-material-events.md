# Native GCYM material events

The pack material context includes the complete original GCYM event subscriber
with Susy-Core's original `GCYMEventHandlersMixin`. This advances material-edit
feedback; it does not complete addon discovery, generated content or recipes.

## Source and execution contract

The selected pack hashes GCYM 1.2.11 and Susy-Core 0.1.118. GCYM callback bodies
and dependencies are inspected in that exact native artifact. Susy's source and
mixin configuration are pinned in `sources/material-program.lock.json`.
The native artifact inventory binds every original class and resource hash.
No callback bodies, material declarations or property rules are reimplemented.

Susy's late loader normally selects its GCYM configuration through mod presence.
Axiom selects the material-event mixin from the explicitly supplied, pack-hashed
GCYM/Susy inputs. This is bounded composition, not execution of native plugin
discovery. The original refmap and complete mixin class are retained; unrelated
GCYM block/machine transforms remain excluded and recorded as such.

Before registration, Axiom requires the transformed GCYM subscriber to contain
the original merged mixin. Original Forge EventBus registers its complete class
under a GCYM owner and the previous active owner is restored. GCYM is registered
before Susy, consistent with Susy's `required-after:gcym` dependency. This does
not qualify ordering relative to the remaining absent addons.

During the native material event, Susy's original HEAD injection cancels GCYM's
material producer. Executing GCYM's untransformed catalog would not be pack parity.
In the post-material event:

1. Susy's HIGH-priority callback runs before GCYM's NORMAL-priority callback.
2. GCYM's original alloy-property pass inspects registered material components,
   blast/fluid properties and disabling flags. Its original late-flag pass
   installs custom recipe producers on two GT materials; recipes are not run.
3. Susy's injection forces molten generation for six named alloys before GCYM's
   fluid handler. A missing named material is skipped; a present material without
   its alloy property throws the original exception.
4. GCYM's original handler queues molten fluid builders where eligible. Queuing
   is not fluid registration, generated-item registration or recipe processing.

The six names are `susy:monel_500`, `susy:hsla_980_x`,
`susy:food_grade_stainless_steel`, `susy:zircaloy_4`, `susy:reactor_steel` and
`susy:alnico`. Axiom does not special-case these names in execution; they belong
to the original mixin. Regression witnesses inspect their resulting native state.

## Feedback and evidence

`execution.gcymSubscriber` reports original handlers, owner restoration and
observed mixin application. Native dispatch snapshots retain actual listener
positions. They are not a completed per-listener invocation trace.
The existing property observer reads attached original alloy properties without
invoking verification or recipe producers. Deferred-fluid observations inspect
existing queues without draining them or registering fluids.

Complete fresh-worker programs cover alloy addition, temperature changes, the
native hot-ingot threshold, ineligible single-component materials, removal,
forced low-temperature molten generation, the original missing-property error,
developer correction and fresh baseline isolation. The unchanged full saved pack
is also checked, including all six forced alloys. These are execution witnesses,
not an independent whole-game parity oracle.

The result remains **check incomplete** while other addon lifecycle and generated
block/item prerequisites are absent. No automatic source correction, synthetic
native rule, Minecraft launch or full-pack latency claim is introduced.
