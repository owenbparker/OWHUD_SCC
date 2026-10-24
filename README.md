# SCC Overwatch HUD

An Overwatch 2 overlay and map bans graphic for St. Clair Saints broadcasts.
Everything is set by hand in the control panel (nothing reads the game): the
teams, who won each map, and the map bans.

## Install

Download **SCC-OW-HUD-Setup.exe** from the
[latest release](https://github.com/owenbparker/OWHUD_SCC/releases/latest)
and run it (no admin rights needed; it installs to `C:\SCC Overwatch HUD`).
The app checks for updates when it starts, and the SCC HUD Manager can
install and update it too.

## Outputs (1920×1080 browser sources in vMix / OBS)

- `http://localhost:8785/overlay`: the in-game overlay. The **HUD** dropdown
  picks the design: **SCC 1** ("OVERWATCH OVERLAY"), **SCC 2** ("OW
  OVERLAY") or **SCC 3** ("NEW OW OVERLAY"). `?style=1|2|3` on the URL fixes
  one design for that source.
- `http://localhost:8785/map-bans`: the map bans ("OW2 Maps Modes"), over the
  SCC background loop (`?bg=0`: no background).

Both are also in the SCC GFX Engine under **Game HUD** once the HUD is
connected (GFX Engine page); the engine puts its own background loop behind
the map bans.

## Control panel

- **Match**: *Won the map* gives the map being played to a team (its maps won
  go up on the overlay and the map bans), *Map drawn*, the series length
  (first to 2, 3 or 4) and *New series*. The overlay's design and options
  (team logos, records, SCC 1's coloured slashes, SCC 3's accent colour, up /
  down).
- **Teams**: names, short names, records, colours and logos (*Swap sides*
  switches the teams over, with their maps won, Team A and stats); the tournament
  logo (middle of SCC 3, top of the map bans) and the line under it.
- **Map Pool**: the maps on the graphic, a column per mode; *Current
  competitive pool* (OWCS 2026 Stage 3: Busan, Ilios, Oasis · Eichenwalde,
  Midtown, Paraíso · Grímsvötn, Havana, Route 66 · Esperança, New Queen Street,
  Runasapi · Aatlis, New Junk City, Suravasa). *Picture* replaces a map's
  picture (every map in the pool has one built in; Ilios, Paraíso and
  Grímsvötn are Blizzard's own screenshots, via the OverFast API, styled like
  the others).
- **Map Bans**: the veto, step by step, with who won each map.
  - **OWCS** (2025-26 Official Rules 9.4): map 1 is Control, picked by Team A
    (the higher seed). After each map the team that lost it picks the next
    map, from a mode not played yet (once every mode has been played, any mode
    not played since). A map is never played twice; there are no map bans.
  - **NACE** (the FACEIT veto): each map has a fixed mode; the teams ban maps
    of that mode in turn, then a team picks one. First to 3: Control (A ban, B
    ban, A picks), Hybrid (B, A, B picks), Push (B ban, A picks), Flashpoint
    (B picks), Escort (A, B, A picks); tiebreakers Control then Push.
  - A drawn map adds a map to the series.
- **Stats**: the post-game stats: each team's 5 players, their hero (every
  hero, by role) and E / A / D / DMG / H. Drop a screenshot of the scoreboard
  on the page (or paste it with Ctrl+V, or click to choose one) and the names
  and numbers are read from it with Windows' text recognition: a player goes
  to the row with their name, the teams to the sides whose names match, and
  anything that couldn't be read is listed to type in. (Each build reads a
  made-up scoreboard to check this; the heroes aren't read, pick them.) *Swap the teams* if
  they landed the wrong way round; *Clear the stats* for the next map. They go to the GFX Engine's Team
  stats (DMG, Kills, Deaths) and Player stats (the same + Healing).

Long names and lines shrink to fit their boxes on every design and on the map
bans.

Hero pictures are downloaded (Blizzard's portraits, via the OverFast API) into
`heroes\` next to the app the first time the HUD is online; a picture can also
be put there by hand, named like `soldier-76.png`.

Fonts: the designs' own fonts are used when they're installed on the PC
(Chinese Rocks for SCC 1, Industry for SCC 3, Dharma Gothic E for the map
bans); otherwise Bebas Neue, Saira Condensed and Tungsten stand in.

## GFX Engine fields

"Overwatch HUD": `left_name`, `left_short`, `left_record`, `left_score` (and
`right_…`), `first_to`, `best_of`, `map_number`, `map_name`, `map_mode`,
`info`, and for each map `map1_name`, `map1_mode`, `map1_winner`…; the
stats: `left_p1_name`, `left_p1_hero`, `left_p1_hero_icon`, `left_p1_kills`,
`left_p1_assists`, `left_p1_deaths`, `left_p1_damage`, `left_p1_healing`
(p1 to p5, and `right_…`). The engine's player pictures show the heroes when
they're set to champions / agents / heroes.
**Use Global Information** takes the engine's team names, colours, logos and
tournament logo, and the players' names for the stats.

## From source

    python -m owhud.gui          # the desktop app
    python -m owhud              # the HUD alone
    python -m unittest discover -s tests
    python -m owhud.scoreboard shot.png   # what a screenshot reads as (Windows)
