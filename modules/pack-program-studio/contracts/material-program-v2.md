# Groovy material source program v2

Status: active bounded source projection

`workbench-pack-material-program-v2` projects V2 material declarations into
three source roles: direct registrations, generator templates with retained
source identity, and finite call-site specializations. It also emits ordered
material mutation candidates.

Specialization is limited to exact helper parameters, profile constants,
material-symbol identities, Boolean guards, integer arithmetic, string
concatenation, builder-variable calls, and profile-admitted method summaries.
Every expanded registration retains both the template span and call-site span.
An excluded exact guard emits no registration; an unresolved guard remains a
frontier specialization.

Mutations identify their target material independently from registration
ownership. Their constraints express only the required additions or values:
flags and transitive required flags, verified property additions, presentation
values, formula values, and separately requested fluid storage keys. A
mutation does not declare the target's complete material core.

Pack material symbols resolve only through the source file that declares them
or an exact admitted static import. Recognized mutation syntax is never
silently discarded: unresolved receivers, flags, property keys, values, and
helper storage arguments emit `frontier-static` mutation declarations with
their source occurrence and uncertainty codes.

The projection does not execute Groovy, reflection, arbitrary closures,
dynamic dispatch, metaclass bodies, or unbounded loops. Runtime execution and
transition order remain Crucible authority.
