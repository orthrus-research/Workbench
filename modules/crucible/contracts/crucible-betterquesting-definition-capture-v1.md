# Crucible BetterQuesting definition capture V1

Status: qualified experimental profile adapter in cohort `m5-progression-005`

The `betterquesting-definitions` adapter captures the final definition state of
BetterQuestingUnofficial 4.3.2 on the exact Supersymmetry provisional Cleanroom
profile. It serializes definitions through BetterQuesting's definition-only
NBT APIs and emits independently identified quests, tasks, rewards,
prerequisites, quest lines, line placements, item requirements, fluid
requirements, and reward-item occurrences.

Quest identity is the pack-profile-bound integer quest ID. Task and reward
identity is occurrence-local to a quest. A line placement is not a property of
quest identity. Item and fluid values are occurrence records and join the
shared Atlas item/fluid classifications only when their serialized definitions
contain those relationships.

The adapter does not call task detection, reward claiming, quest submission,
client GUI, or player-state APIs. Completion, claim, party, life, and task
progress data are excluded. Two canonical samples must agree and the adapter
fails closed if the exact definition universe drifts.

Producer `0.8.0` passed the 34-adapter gate with zero unsupported values or
diagnostics. The category contains 6,369 records: 1,061 quests, 30 quest lines,
1,210 tasks, 51 rewards, 1,354 prerequisites, 1,181 line placements, 1,238
item requirements, 178 fluid requirements, and 65 reward-item occurrences.
This qualification is cohort-, artifact-, side-, pack-revision-, and
provisional-platform-specific.
