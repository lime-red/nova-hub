"""The games' own help for every reset setting, word for word.

Copied from the help files BRE and FE show in the reset editor (RESET.HLP),
with the colour codes and the decorated title line removed. The title is kept
separately: the driver checks it to know which field the editor opened. Two
lines the editor prints after a toggle ("This option has been toggled", "The
setting for this option has been switched") are dropped; they are not help.
Typos are the games', kept as they are.

Generated once from the .HLP files and committed; the games will not change.
Immortal Barons has no help file: its text is from its own config editor, below.
"""

# field id -> (help title as the editor shows it, help text)
BRE = {
    'start_date': (
        'Start Date',
        'The Start Date Configuration allows you to reset the game earlier than\n'
        'actually starting.  This option is very useful for League Coordinators\n'
        'in Inter-BBS games to force all systems to start on the same date (using\n'
        'league-wide reset).  This option is also useful for Tournament games in\n'
        'which you want to allow players to sign up early.'
    ),
    'join_date': (
        'Join Date',
        'The Join Date option allows the sysop (or LC) to determine the last\n'
        'date a player can join the game.  This is useful for restricting access\n'
        'to latercomers in the game for tournaments, etc.'
    ),
    'turns_per_day': (
        'Turns Per Day',
        'The number of turns per day controls how quickly a game will progress.\n'
        'Normal games are generally around 8 turns per day, while quick games\n'
        'may go to 10-12.  The maximum amount, 20, will create a very quick\n'
        'paced game that will consume large amounts of player time and is\n'
        'recommended to only be used in rare instances.'
    ),
    'protection_turns': (
        'Turns Of Protection',
        'The number of turns of protection controls the number of turns for\n'
        'which a new empire is unable to attack, trade, and be attacked.  This\n'
        'is to allow small newcomers to join the game without immediately\n'
        'being destroyed.'
    ),
    'initial_land': (
        'Initial Market Land',
        'This option refers to the amount of land available to buy for a player\n'
        'at the beginning of the game.  Land is also created daily and a new\n'
        'player always has the option of buying up to 300 regions of land.  An\n'
        'initial value of 0 will give no advantage to the first few players,\n'
        'while a medium or high setting, depending on the number of turns per\n'
        'day, may be advantageous to the first players.'
    ),
    'land_per_day': (
        'Daily Land Creation',
        'This amount is the number of regions added to the market each day.  The\n'
        'regions are added as the day progresses.  The larger the value here, the\n'
        'faster growth each BBS in the game will have.  Smaller values will\n'
        'generally base strength more on attacks.'
    ),
    'interest_rate': (
        'Bank Interest Rate',
        'This is the amount of interest the bank will give in 10 days.  Interest\n'
        'is given to the player at the end of each turn.  In many InterBBS games,\n'
        'the interest rates and investments in the banks are exploited to an\n'
        'extent making the game investment-oriented; for these games, we highly\n'
        'recommend a low interest value (below 40%).  The maximum value, 200%, is\n'
        'equivalent to 20% interest per day in previous versions versions below\n'
        'v0.950.'
    ),
    'investment_rate': (
        'Basic Investment Interest Rate',
        'This is the amount of interest the bank will give in 10 days on investments\n'
        'at the beginning of the game.  Each day, if the Steady Investment Rate\n'
        'option is not enabled, the rate will change based on the amount of\n'
        'investing on the system, but will always be centered around this value. In\n'
        'many InterBBS games, the interest rates and investments in the banks are\n'
        'exploited to an extent making the game overflow; for these games, we highly\n'
        'recommend a low interest value (below 50%).  The maximum value, 100%, is\n'
        'equivalent to 10% interest per day.\n'
        'day.'
    ),
    'steady_investment': (
        'Steady Investment Interest Rate',
        'With this option disabled, investment interest rates will drop (possibly\n'
        'down to 1/2 the initial value) if there is too much investing going on.\n'
        'On the other end, with little investing going on, the rate can double the\n'
        'initial value.  If you do not wish have this rate changing, you can enable\n'
        'this option.'
    ),
    'tax_rate': (
        'Planetary Tax Rate',
        'The Tax rate can be used to control the growth of empires.  A high tax\n'
        'rate will generally equalise the size of baronies by "stealing from the\n'
        'rich and giving to the poor", while low tax rates allow large empires to\n'
        'grow much more easily.  Tax Refunds given daily are based on the total\n'
        'amount of income the BBS recieves in taxes.  Randomly throughout the\n'
        'game, the "BBS emperor" will raise or lower tax rates; therefore, the\n'
        'value set here is not binding, but provides an average, and initial,\n'
        'value for the game.'
    ),
    'max_region_purchase': (
        'Maximum Region Purchases',
        'This option limits the number of regions a player can buy in a single turn\n'
        'to prevent players with large amounts of cash from suddenly "jumping" in\n'
        'regions.  This value works in combination with the region pricing system,\n'
        'which can scale costs upward, except when a small\n'
        'empire receives large amounts of cash from an ally.'
    ),
    'max_players': (
        'Maximum Players',
        'This setting determines the maximum number of players on a system.  The\n'
        'normal maximum is 25, but smaller leagues may wish to lower the value\n'
        'to distribute players more evenly.  Leagues consisting primarily of local\n'
        "BBS's may wish to use this option to make game play more competitive by\n"
        'dividing the players up.'
    ),
    'buy_military': (
        'Buy Military',
        'This setting allows you to control military purchasing.  The "Yes" option\n'
        'creates a game in which military purchasing is unlimited, as in all versions\n'
        'before v0.960.  The "No" setting disables all military purchasing, forcing\n'
        'forcing players to produce using industrial regions.  The "Limited" option\n'
        'creates a limited amount of military in the market each day for purchasing.\n'
        'The "No" and "Limited" options force new strategies, reducing the usefulness\n'
        'of massive investing, while increasing the importance of owning regions.\n'
        'Additionally, overnight armies become almost impossible.'
    ),
    'maintenance_costs': (
        'GamePlay: Maintenance Costs',
        'The GamePlay options may be set to the following values: High, Medium\n'
        'Low, or None.  [H,M,L,N]  Some settings will not be allowed for certain\n'
        'options.  The Maintenance cost option sets the amount that must be paid to\n'
        'maintain military & regions in the pay-section of the game.  The higher\n'
        'setting will cause slower & more controlled growth rates, as well as smaller\n'
        'militaries.\n'
        'It may be set to either High, Medium, Low, or None - Default is Medium.'
    ),
    'trade_costs': (
        'GamePlay: Trade Costs',
        'The GamePlay options may be set to the following values: High, Medium\n'
        'Low, or None.  [H,M,L,N]  Some settings will not be allowed for certain\n'
        'options.  The Trade Cost option affects the amount of money charged when\n'
        'sending out trade deals.  A Low setting will encourage trading while a high\n'
        'setting limits trading to more important times.\n'
        'It may be set to either High, Medium, Low, or None - Default is Medium.'
    ),
    'region_costs': (
        'GamePlay: Region Costs',
        'The GamePlay options may be set to the following values: High, Medium\n'
        'Low, or None.  [H,M,L,N]  Some settings will not be allowed for certain\n'
        'options.  The Region Cost option affects the amount of money charged when\n'
        'purchasing new regions.  A Low setting will encourage purchases while a high\n'
        'setting makes war a more viable alternative.  The None setting will cause a\n'
        'minimal price increase in regions as purchased.\n'
        'It may be set to either High, Medium, Low, or None - Default is Medium.'
    ),
    'attack_damage': (
        'GamePlay: Attack Damage',
        'Some settings will not be allowed for certain\n'
        'options.  The Attack Damage option defines the amount of forces destroyed in\n'
        'combat.  A High setting will likely reduce combat & make strategy more\n'
        'important, while a low setting will make constant warfare with less strategy\n'
        'a more useful tactic.\n'
        'It may be set to either High, Medium, or Low - Default is Medium.'
    ),
    'attack_rewards': (
        'GamePlay: Attack Region Gains',
        'Some settings will not be allowed for certain\n'
        'options.  The Attack Region Gains option affects the number of regions won\n'
        'in all attacks. A High setting will make attacking more devastating while a\n'
        'low setting will make it more difficult to destroy smaller empires.\n'
        'It may be set to either High, Medium, or Low - Default is Medium.'
    ),
    'sabre': (
        'Sabre Handling',
        'The S3-Sabre is a variable-return missile.  Capable of a variety of\n'
        'special effects, this missile, when controlled, can be extremely\n'
        'devastating.\n'
        '\n'
        'Beginning with version 0.988 of BRE, the Sabre missle feature may be\n'
        'controlled via a configurable option as some players find the Sabre\n'
        'feature unfair due to the huge impact that it can have on game play.'
    ),
    'attack_costs': (
        'GamePlay: Attack Costs',
        'The GamePlay options may be set to the following values: High, Medium\n'
        'Low, or None.  [H,M,L,N]  Some settings will not be allowed for certain\n'
        'options.  The Attack Cost option affects the amount of money charged when\n'
        'sending InterBBS attacks. A High setting will make attacking more\n'
        'difficult for larger empires.\n'
        'It may be set to either High, Medium, Low, or None - Default is Medium.'
    ),
    'terrorist_costs': (
        'GamePlay: Terrorism Costs',
        'The GamePlay options may be set to the following values: High, Medium\n'
        'Low, or None.  [H,M,L,N]  Some settings will not be allowed for certain\n'
        'options.  The Terrorism Cost option affects the amount of money charged when\n'
        'sending InterBBS Terrorists. A High setting will make terrorism less\n'
        'popular.\n'
        'It may be set to either High, Medium, Low, or None - Default is Medium.'
    ),
    'indiv_attacks': (
        'InterBBS: Max Individual Attacks',
        'The number of individual Inter-BBS Attacks allowed per day should generally\n'
        'be kept low to prevent larger empires from destroying a smaller opponent\n'
        'in several strikes at once.  For some leagues, though, such as free-for-all\n'
        'leagues or whatever, you may wish to experiment with higher values. :)'
    ),
    'group_attacks': (
        'InterBBS: Max Group Attacks',
        'The number of group Inter-BBS Attacks allowed per day should generally\n'
        'be kept low to prevent larger empires from destroying a smaller opponent\n'
        'in several strikes at once.  For some leagues, though, such as free-for-all\n'
        'leagues or whatever, you may wish to experiment with higher values. :)'
    ),
    'terrorist_ops': (
        'InterBBS: Max Terrorist Operations',
        'The number of terrorist operations allowed per day can now be configured\n'
        "to fit your league's desire.  It is highly recommended to keep a fairly\n"
        'small value here, such as 10 or 15.'
    ),
    'bombings': (
        'InterBBS: Max Bombing Operations',
        'The number of bombings operations allowed per day can now be configured\n'
        "to fit your league's desire.  Originally, it was infinite, but often\n"
        'literally hundreds of bombing operations are sent and I thought I should\n'
        'offer some type of control for this.  We recommend a setting around\n'
        '5 or 10 ops a day.'
    ),
    'lost_attack_days': (
        'InterBBS: Lost Attack Prevention',
        'BRE supports logging of attacks and lost attack prevention.  If an attack\n'
        'has not returned after a certain number of days, the forces will be\n'
        'returned to the owner and the attack voided.  If the attack is to return\n'
        'in the future, it will not be processed, pretending that the assault was\n'
        'never made.  The number of days should be kept a little bit larger than\n'
        'the maximum turn around time for attacks from one BBS to another.  League\n'
        'Coordinators, please be sure to take into consideration the maximum time\n'
        'from any system in your league to any other, as the values you set will\n'
        'be used at all other systems in your league.'
    ),
    'gooie': (
        'InterBBS: Gooie Kablooies',
        'One of the special forms of attacks in BRE is to send a "Gooie Kablooie"\n'
        'to another BBS.  It is an expensive, but often powerful device.  In certain\n'
        'games, this feature may be harmful to gameplay.  If you are designing your\n'
        'setup to be primarily region-oriented (low interest rates, limited or no\n'
        'buying of military, etc), you may wish to consider disabling this feature\n'
        'so as to add more parity to the game.'
    ),
    'bombing_ops': (
        'InterBBS: Bombing Operations',
        'One of the special forms of attacks in BRE is to send bombs to destroy\n'
        'Food Markets, Investments, Trading Markets, etc. on another BBS.  Depending\n'
        'on your setup, you may wish to disable this feature.  We generally\n'
        'recommend leaving Bombing Operations enabled for most setups.'
    ),
    'missile_ops': (
        'InterBBS: Missile Operations',
        'One of the special forms of attacks in BRE is to send Nuclear, Chemical,\n'
        'and other special missiles to destroy enemy facilities.  Depending on your\n'
        'setup, you may wish to disable this feature (one example may be in heavily\n'
        'region-oriented games).  We generally recommend leaving Missile Operations\n'
        'enabled for most setups.'
    ),
    'local_attacks': (
        'InterBBS: Local Attacks',
        'This option enables or disables the ability to attack other local empires\n'
        'in Interplanetary games.  Leaving this option enabled,\n'
        "forces BBS's to use teamwork to remove troublemakers and allowing a\n"
        'method to destroy spies, etc.  Leaving it disabled limits the ability of moles\n'
        'to cause local damage during a campaign.'
    ),
    'local_scoring': (
        'InterBBS: Local Attack Scoring',
        'Enabling this option in an Interplanetary game will make BRE add score\n'
        'points to players who attack and defeat local opponents.  When disabled,\n'
        'score cannot be achieved through local fighting.  We recommend disabling\n'
        'this option, so that users cannot attack each other just to build up\n'
        'score.'
    ),
    'dupe_checking': (
        'InterBBS: Duplicate User Checking',
        'Enabling this feature will make BRE look for users on your system that may\n'
        'be playing on other BBSes and temporarily lock them out of the game (until\n'
        "they delete one of their players).  In general, long-distance games don't\n"
        'require this option as much as local games may.  Leaving this feature OFF\n'
        'will save some data transferring, but allows players to play on several\n'
        'systems more easily.'
    ),
    'league_wide': (
        'InterBBS: League-Wide Reset',
        'Selecting this will send reset information to all other systems in the\n'
        'league, automatically resetting their games without sysop intervention.\n'
        'This can be used if you wish to end league play and restart the game\n'
        'fresh or if problems have developed requiring a league-wide reset.'
    ),
}

FE = {
    'start_date': (
        'Start Date',
        'The Start Date Configuration allows you to reset the game earlier than\n'
        'actually starting.  This option is very useful for League Coordinators\n'
        'in Inter-BBS games to force all systems to start on the same date (using\n'
        'league-wide reset).  This option is also useful for Tournament games in\n'
        'which you want to allow players to sign up early.'
    ),
    'join_date': (
        'Join Date',
        'The Join Date option allows the sysop (or LC) to determine the last\n'
        'date a player can join the game.  This is useful for restricting access\n'
        'to latercomers in the game for tournaments, etc.'
    ),
    'turns_per_day': (
        'Turns Per Day',
        'The number of turns per day controls how quickly a game will progress.\n'
        'Normal games are generally around 6-8 turns per day, while quick games\n'
        'may go to 10 or 12.  The maximum amount will create a very quick paced game\n'
        'that will consume large amounts of player time and is recommended to\n'
        "only be used in rare instances.  Until you're experienced with the game,\n"
        'we recommend staying around 8, as turns can involve a variety of things\n'
        'and a high number here can consume alot of time.'
    ),
    'protection_turns': (
        'Turns Of Protection',
        'The number of turns of protection controls the number of turns for\n'
        'which a new empire is unable to attack, trade, and be attacked.  This\n'
        'is to allow small newcomers to join the game without immediately\n'
        'being destroyed.'
    ),
    'initial_land': (
        'Initial Market Land',
        'This option refers to the amount of land available to buy for a player\n'
        'at the beginning of the game.  Land is also created daily and a new\n'
        'player always has the option of buying up to 300 acres of land.  An\n'
        'initial value of 0 will give no advantage to the first few players,\n'
        'while a medium or high setting, depending on the number of turns per\n'
        'day, may be advantageous to the first players.'
    ),
    'land_per_day': (
        'Daily Land Creation',
        'This amount is the number of acres of land added to the market each day.\n'
        'The larger the value here, the faster growth the BBS is capable of.\n'
        'Smaller values will generally base strength more on attacks.'
    ),
    'wages': (
        'Standard Wages',
        'This amount refers to the number of silver coins a standard human worker is\n'
        'paid per month.  Lowering the value from the default will make people\n'
        'cheaper to hire, allowing players to devote less to money-making labor.\n'
        'This leads to faster growth rates, but make give larger empires a\n'
        'significant advantage.  The opposite is true of raising the cost of a\n'
        'worker.'
    ),
    'start_land': (
        'Starting Land',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_people': (
        'Starting People',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_silver': (
        'Starting Silver',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_food': (
        'Starting Food',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_stone': (
        'Starting Stone Blocks',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_weapons': (
        'Starting Weapons',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'start_runes': (
        'Starting Runes',
        'This series of options allows you to control the start up settings of the\n'
        'players.  Using larger values tends will quicken the pace of the game,\n'
        'but may lead to some imbalances.  If you are not too experienced with the\n'
        'game, we highly recommend using the base settings.'
    ),
    'max_attacks': (
        'Max Attacks',
        'This is the total number of attacks a player can make in one day.  It\n'
        'includes regular local attacks as well as both Individual and Group\n'
        'attacks in InterBBS games.  Because certain races have penalties in their\n'
        'attacks per day, this number should be set to at least 4, if not higher.'
    ),
    'max_spells': (
        'Max Spells',
        'This is the total number of spells a player can cast in one day.  It\n'
        'includes both local and InterBBS spells.  Because certain races have\n'
        'penalties in their spells per day, this number should be set to 5 at a\n'
        'minimum.  However, we recommend a higher setting such as 10 for creating\n'
        'a more interesting game.'
    ),
    'max_players': (
        'Maximum Players',
        'This setting determines the maximum number of players on a system.  The\n'
        'normal maximum is 25, but smaller leagues may wish to lower the value\n'
        'to distribute players more evenly.  Leagues consisting primarily of local\n'
        "BBS's may wish to use this option to make game play more competitive by\n"
        'dividing the players up.'
    ),
    'lost_attack_days': (
        'InterBBS: Lost Attack Prevention',
        'FE supports logging of attacks and lost attack prevention.  If an attack\n'
        'has not returned after a certain number of days, the forces will be\n'
        'returned to the owner and the attack voided.  If the attack is to return\n'
        'in the future, it will not be processed, pretending that the assault was\n'
        'never made.  The number of days should be kept a little bit larger than\n'
        'the maximum turn around time for attacks from one BBS to another.  League\n'
        'Coordinators, please be sure to take into consideration the maximum round\n'
        'trip time from any system in your league to any other, as the values you\n'
        'set will be used at all other systems in your league.'
    ),
    'local_attacks': (
        'InterBBS: Local Attacks',
        'This option enables or disables the ability to attack other local empires\n'
        'in Interplanetary games.  We highly recommend leaving this option enabled,\n'
        "forcing BBS's to use teamwork to remove troublemakers."
    ),
    'local_scoring': (
        'InterBBS: Local Attack Scoring',
        'Enabling this option in an Interplanetary game will make FE add score\n'
        'points to players who attack and defeat local opponents.  When disabled,\n'
        'score cannot be achieved through local fighting.  We recommend disabling\n'
        'this option, so that users cannot attack each other just to build up\n'
        'score.'
    ),
    'dupe_checking': (
        'InterBBS: Duplicate User Checking',
        'Enabling this feature will make FE look for users on your system that may\n'
        'be playing on other BBSes and temporarily lock them out of the game (until\n'
        "they delete one of their players).  In general, long-distance games don't\n"
        'require this option as much as local games may.  Leaving this feature OFF\n'
        'will save some data transferring, but allows players to play on several\n'
        'systems more easily.'
    ),
    'league_wide': (
        'InterBBS: League-Wide Reset',
        'Selecting this will send reset information to all other systems in the\n'
        'league, automatically resetting their games without sysop intervention.\n'
        'This can be used if you wish to end league play and restart the game\n'
        'fresh or if problems have developed requiring a league-wide reset.'
    ),
}


# Immortal Barons: the help its own config editor shows for each setting
# (internal/menu/config_editor_tui.go, v0.2.6), word for word. IB is MIT
# licensed, (c) Andy Alt and contributors; https://github.com/andy5995/immortal-barons.
# IB words its help itself rather than copying BRE's.
IB = {
    'start_date': (
        'Game Start Date',
        'The date the game starts. Before it, players can join and play their '
        'first turns, but no new day begins. Blank starts the game right away.'
    ),
    'join_date': (
        'Game Join Date',
        'New players may not join after this date. Blank leaves joining open.'
    ),
    'turns_per_day': (
        'Turns Per Day',
        'How many turns each player may take per day.'
    ),
    'protection_turns': (
        'Turns of Protection',
        'New-realm protection: turns a fresh empire cannot attack and cannot be'
        ' attacked, giving newcomers time to get established.'
    ),
    'game_length': (
        'Game Length',
        "Game days before this board's game ends. The realm with the most "
        'regions is crowned Planetary Master, every realm is removed, and a new'
        ' game starts. 0 means the game never ends.'
    ),
    'idle_days_remove': (
        'Days Idle Before Removal',
        'Days a realm may go unplayed before it is removed. A realm whose owner'
        ' never played a turn is removed the next day. 0 never removes '
        'anything.'
    ),
    'initial_land': (
        'Initial Market Land',
        'Extra regions each new realm may buy when it is created, on top of its'
        " first day's allowance."
    ),
    'land_per_day': (
        'Land Created / Day',
        'Regions added each day to what each realm may buy.'
    ),
    'interest_rate': (
        'Interest Rate',
        'Bank interest over 10 days: 50 is 5% a day, and the maximum (200) is '
        '20% a day. It is paid in equal parts at the end of each turn a player '
        'takes. High rates let banking dominate the game, so a value near the '
        'minimum keeps play balanced.'
    ),
    'investment_rate': (
        'Standard Investment Rate',
        'The investment return over 10 days when Steady Investment Rate is on: '
        '35 is 3.5% a day, and the maximum (100) is 10% a day. When Steady '
        'Investment Rate is off, the return drifts each day with how much is '
        'invested, and the bank pulls it back when it falls below half of this '
        'value or rises above one and a half times it.'
    ),
    'steady_investment': (
        'Steady Investment Rate',
        'When on, the investment return stays fixed at the Standard Investment '
        'Rate instead of drifting each day with how much is invested.'
    ),
    'tax_rate': (
        'Max Tax Rate',
        'The highest tax rate a player is allowed to set.'
    ),
    'planetary_tax_rate': (
        'Planetary Tax Rate',
        "The crown tax the Queen Royale takes from each turn's gold income, as "
        "a whole percent (default 5, maximum 20). The gold goes to the Queen's "
        'purse, which she refunds a share of to each baron at the start of '
        'their playing day.'
    ),
    'max_region_purchase': (
        'Max Purchasable Regions',
        'The most regions a player may buy in one turn. It does not limit how '
        'many regions a player may own. 0 means no limit.'
    ),
    'max_players': (
        'Max Players',
        'The most players on this board, 1 to 25. 0 means as many as the planet'
        ' has room for (25).'
    ),
    'food_unlimited': (
        'Food Unlimited',
        'When on, the food market never runs short — its daily supply is '
        'unlimited.'
    ),
    'buy_military': (
        'Buy Military',
        'Whether players may buy military units. Yes: they may buy them freely.'
        ' No: they must build them with industry. Limited is not built yet and '
        'works like Yes.'
    ),
    'maintenance_costs': (
        'Maintenance Costs',
        'Upkeep cost for regions and forces: High, Medium, Low, or None.'
    ),
    'trade_costs': (
        'Trade Deal Costs',
        'Cost of trade deals: High, Medium, Low, or None.'
    ),
    'region_costs': (
        'Region Costs',
        'How fast the price of a region rises for a realm with 300 or more '
        'regions. Smaller realms and realms under protection are not affected. '
        'None removes the extra rise; it does not make regions free.'
    ),
    'attack_damage': (
        'Attack Damage',
        'How much of its force each side loses before it breaks off a battle: '
        'High, Medium, or Low. It applies to attacks on this board and on other'
        ' boards.'
    ),
    'attack_rewards': (
        'Attack Rewards',
        "How large a share of a beaten realm's regions the winner takes: High, "
        'Medium, or Low. Only regions change hands.'
    ),
    'sabre': (
        'Sabre Handling',
        'How S3-Sabre missiles sent to other boards are aimed. User Select: the'
        ' player sets the dial. Random: a random dial for each launch. '
        'Constant: always the dial set below. None: the missile cannot be used.'
    ),
    'sabre_dial': (
        'Sabre Constant Setting',
        'Which dial Constant handling fires (0-10). Ignored by the other modes.'
    ),
    'local_attacks_per_day': (
        'Max Local Attacks',
        'The most attacks a player may make on this board in one day. Regular '
        'attacks, nuclear, chemical and biological attacks, and attacks on '
        'pirates all count. A player can still make only one per turn. 0 means '
        'no daily limit beyond that.'
    ),
    'pirates': (
        'Pirates',
        'Whether there are pirates at all. Off, nobody is raided and Attack '
        'Pirates is gone.'
    ),
    'attack_costs': (
        'Attack Costs',
        'How much gold an attack sent to another board costs to launch.'
    ),
    'terrorist_costs': (
        'Terrorist Costs',
        'How much gold terrorist operations cost to launch.'
    ),
    'indiv_attacks': (
        'Individual Attacks',
        'The most Individual Attack Forces a player may send to other boards in'
        ' one day. 0 means no limit. Attacks on this board are limited by Max '
        'Local Attacks instead.'
    ),
    'group_attacks': (
        'Group Attacks',
        'The most group (interplanetary) attacks a player may lead or join in '
        'one day. 0 means no limit.'
    ),
    'terrorist_ops': (
        'Terrorist Ops',
        'The most terrorist operations a player may launch in one day. Each '
        'agent sent counts as one operation. 0 means no limit.'
    ),
    'bombings': (
        'Bombing Ops',
        'The most bombing operations a player may launch in one day. 0 means no'
        ' limit.'
    ),
    'lost_attack_days': (
        'Lost Forces',
        'Game days to wait for a result from another board before giving up. '
        'Packets go missing; this stops an army being lost for good. Troops, '
        'agents and trade deals come home and market bids are refunded, but '
        'gold spent on special operations is not. 0 means never give up.'
    ),
    'gooie': (
        'Gooie Kablooie',
        'Whether the Gooie Kablooie doomsday weapon may be built.'
    ),
    'bombing_ops': (
        'Bombing Operations',
        'Whether players may send the four bombing operations to other boards: '
        'Bomb Food Market, Bomb Trading Market, Bomb Trade Routes and Undermine'
        ' Investments.'
    ),
    'missile_ops': (
        'Missile Operations',
        'Whether players may send nuclear, chemical and S3-Sabre missiles to '
        'other boards. Attacks on this board are not affected.'
    ),
    'local_attacks': (
        'Local Attacks',
        'Whether barons on this board may attack each other in a league game.'
    ),
    'local_scoring': (
        'Local Attack Scoring',
        'Whether winning a regular attack on a baron on this board earns score.'
        ' The loser never loses score.'
    ),
    'dupe_checking': (
        'Dupe Checking',
        'Whether a baron found playing on another board in the league is locked'
        ' out here.'
    ),
    'ip_trading': (
        'InterPlanetary Trading',
        "Whether barons may bid on an ALLIED planet's Trading Market. IB's own "
        'feature; the original has no such thing.'
    ),
    'min_board_version': (
        'Minimum Game Version',
        'The game version every board in the league must run (blank for no '
        'requirement). A board below it has its packets refused.'
    ),
}
