// Configuration-time only: publish() compiles this into an immutable Java plan.
def studio = mods.worldStudio

studio.defaults()
studio.profile('susy_bop_mega_regions')
studio.megaRegionScale(3072.0d)

// Replace any named octave stack without changing Java generator code.
studio.field('continentalness', 4096.0d, 5, 2.0d, 0.50d)
studio.field('temperature', 2048.0d, 3, 2.0d, 0.52d)
studio.field('moisture', 1536.0d, 4, 2.0d, 0.52d)
studio.field('relief', 640.0d, 4, 2.0d, 0.50d)

// Lithology controls runoff loss and channel erodibility.
studio.lithology('granite', 0.12d, 0.25d)
studio.lithology('stone', 0.38d, 0.65d)
studio.lithology('andesite', 0.22d, 0.38d)

studio.clearMegaRegions()
studio.megaRegion(
    'stable_craton', 4.0d, 67.0d, 10.0d, 0.02d, -0.04d, 'granite',
    'biomesoplenty:prairie',
    'biomesoplenty:seasonal_forest',
    'biomesoplenty:woodland',
    'minecraft:plains'
)
studio.megaRegion(
    'sedimentary_basin', 3.0d, 61.0d, 6.0d, 0.08d, 0.20d, 'stone',
    'biomesoplenty:bayou',
    'biomesoplenty:bog',
    'biomesoplenty:marsh',
    'biomesoplenty:wetland',
    'minecraft:swampland'
)
studio.megaRegion(
    'orogenic_belt', 3.0d, 72.0d, 27.0d, -0.18d, 0.04d, 'andesite',
    'biomesoplenty:alps',
    'biomesoplenty:alps_foothills',
    'biomesoplenty:mountain',
    'biomesoplenty:coniferous_forest',
    'minecraft:extreme_hills'
)

// Coarse grid, Priority-Flood halo, rainfall/runoff thresholds, then channel shape.
studio.watershedGrid(16, 32, 24)
studio.watershedRunoff(0.08d, 1.0d, 0.75d, 8.0d, 45.0d)
studio.watershedChannels(1.5d, 2.0d, 5.0d, 5.0d, 2.0d, 1.5d)
studio.carvers(true, true)
studio.publish()
