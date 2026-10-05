"""Decide whether an article is news, a review, a preview, or something to drop.

Wanted: news and updates on games, the industry and nearby areas (films, TV, hardware), reviews, previews.
Dropped: guides, walkthroughs, answers and solutions, tier lists, rankings, "best X" and top-N lists,
deals and price drops, codes, quizzes and daily puzzles, release countdowns, "should you" pieces.

Checks, first match wins:
  1. Release round-ups ("games coming out this week", "out today") are news.
  2. Strong service signals (guide, tier list, answers, deals, "Top 10"...) drop the article, unless it
     also reports a clear event (layoffs, acquisition, delay, cancellation, announcement, leak).
  3. Review / preview words make it a review or preview.
  4. News words ("announced", "patch", "delayed", "sales", "lawsuit"...) keep it.
  5. Weaker service signals ("explained", "where is", "hints") drop it.
  6. Anything else is kept: when in doubt, keep. Missing real news is worse than one extra feature.
The article's web address counts too: /guides/, /deals/, /lists/, /best/ and similar sections drop it.
"""

import re


def _rx(parts: list[str]) -> re.Pattern:
    return re.compile(r"(?<![\w-])(?:" + "|".join(parts) + r")(?![\w-])", re.IGNORECASE)


RELEASES = _rx([r"(?:coming out|out|launch\w*|releas\w*|arriving) (?:this|next) (?:week|month)", r"out today",
                r"new (?:games|releases) (?:coming|out|this week|this month)", r"games are out", r"games out (?:today|this week)",
                r"new on (?:game pass|ps plus|switch online)", r"(?:leaving|joining) (?:game pass|ps plus)"])

EVENT = _rx([r"layoffs?", r"laid off", r"job cuts", r"acqui(?:res?|red|ring|sition)", r"merger", r"lawsuit", r"sues?", r"sued",
             r"shut(?:s|ting)? down", r"shutdown", r"delay(?:s|ed)?", r"cancel(?:s|led|ed)?", r"announc\w*",
             r"leak(?:s|ed)?", r"revealed", r"studio clos\w*"])

STRONG_DROP = _rx([
    r"guides?", r"walkthroughs?", r"tier list", r"how to", r"how do (?:you|i)", r"where to (?:find|get|buy|pre-?order|watch)",
    r"answers?", r"solutions?", r"solved", r"wordle", r"connections hints", r"crossword", r"quiz(?:zes)?",
    r"\btop \d+\b", r"\b\d+ best\b", r"best (?:weapons?|builds?|loadouts?|settings|classes|perks|cards?|decks?|teams?|"
    r"characters?|skills?|players|armou?r|gear|mods|games|new games|video games|new video games|ps5 games|xbox games|switch games|"
    r"pc games|controllers?|headsets?|monitors?|tvs?|laptops?|keyboards?|mice|mouse|chairs?|deals?)",
    r"ranked", r"every .{1,40} ranked", r"ranking (?:every|all)", r"(?<!a )(?<!the )deals?(?! worth)(?! with)", r"\b\d+% off\b", r"save \$\d+", r"just \$\d+",
    r"drops? to \$\d+", r"lowest price", r"all-time low", r"prime day", r"black friday",
    r"cyber monday", r"redeem codes?", r"\bcodes\b", r"codes? (?:for|list)", r"locations?", r"release (?:time|countdown)(?! confirmed)",
    r"exact date and time", r"what time does", r"when does", r"when is .{1,40} (?:coming|out|releasing)",
    r"should you(?: \w+)?", r"is .{1,40} worth (?:it|buying|playing)", r"choices and (?:outcomes|consequences)",
    r"every (?:fan|player|gamer) (?:needs|should)", r"needs to (?:play|experience)", r"must-?play", r"you need to play",
    r"games like", r"tips(?: and| &) tricks", r"beginner'?s", r"cheats?", r"wallpapers?", r"giveaway",
    r"site survey", r"reader survey", r"enhance your .{1,40} experience", r"essentially .{1,40} meets",
    r"is like .{1,40} meets", r"(?:steam )?game is like",
    # Japanese, for headlines that couldn't be translated
    r"攻略", r"ランキング", r"セール", r"おすすめ", r"クーポン", r"割引",
])
# "12 Brilliant Cosplays...", "7 Games That...": a list headline starting with a number (not "3 million", "11-year-old").
# "20 Brilliantly Inventive Cosplay Fits...", "10 Essential NES Games...": a headline that opens with a count
# of things. Not "3 million", "11-year-old", or a count of people ("3 Warhammer 40,000 sickos share...").
NUMBERED_LIST = re.compile(r"^\s*\d{1,3}\+?\s+(?!(?:million|billion|thousand|years?|days?|hours?|weeks?|months?|"
                           r"minutes?|players?|copies|units|percent|new\s+games\s+are\s+out)\b)[A-Za-z]", re.IGNORECASE)
PEOPLE_COUNT = re.compile(r"^\s*\d{1,3}\s+(?:[\w,.'’-]+\s+){0,3}(?:sickos|fans|devs?|developers|studios|people|"
                          r"workers|employees|staff|players|streamers|creators)\b", re.IGNORECASE)

REVIEW = _rx([r"review(?:s|ed)?(?=[\s:–—-]|$)", r"the \w+ review", r"reviews? (?:are in|round ?up)", r"round ?up: the first reviews",
              r"verdict", r"tested", r"benchmarked"])
NOT_REVIEW = _rx([r"(?:steam|user|player|positive|negative|mixed|overwhelmingly \w+) reviews?", r"review[- ]bomb\w*",
                  r"best-reviewed", r"under review", r"reviews? (?:from|by) players"])
PREVIEW = _rx([r"preview(?:s|ed)?", r"hands[- ]on", r"impressions?", r"we played", r"i played", r"after \d+ hours"])

NEWS = _rx([
    r"announc\w*", r"reveal\w*", r"unveil\w*", r"confirm\w*", r"delay\w*", r"postpon\w*", r"cancel\w*",
    r"launch(?:es|ed)?", r"releas(?:es|ed)", r"out now", r"available now", r"shadow[- ]drop\w*", r"leak\w*",
    r"rumou?r\w*", r"reportedly", r"report", r"datamin\w*", r"teas\w*", r"trailer", r"patch(?:es|ed)?", r"patch notes",
    r"update[sd]?", r"hotfix", r"season \d+", r"roadmap", r"expansion", r"dlc", r"sequel", r"remaster\w*", r"remake",
    r"port(?:ed|s)?", r"layoffs?", r"laid off", r"job cuts", r"strike\w*", r"union\w*", r"acqui\w*", r"merger",
    r"invest\w*", r"funding", r"lawsuit", r"sues?", r"sued", r"court", r"settle\w*", r"ban(?:s|ned)?", r"regulat\w*",
    r"earnings", r"revenue", r"profits?", r"shares", r"ceo", r"president", r"steps down", r"resign\w*", r"hire[sd]?",
    r"joins", r"leaves", r"returns?", r"studio", r"shut(?:s|ting)? down", r"service ends?", r"end of service",
    r"servers?", r"sales", r"sells?", r"sold", r"million", r"record", r"charts?", r"best[- ]selling", r"interview",
    r"showcase", r"direct", r"state of play", r"awards?", r"winners?", r"beta", r"early access", r"demo", r"playtest",
    r"exclusive", r"console", r"hardware", r"subscription", r"game pass", r"ps plus", r"pre-?orders?", r"release date",
    r"coming (?:to|in|on|soon)", r"arrives?", r"collab\w*", r"crossover", r"movie", r"film", r"tv (?:show|series)",
    r"casts?", r"mods?", r"modders?", r"emulat\w*", r"esports?", r"tournament", r"champion\w*",
])

WEAK_DROP = _rx([r"explained", r"where is", r"hints?", r"unlock(?:ing)? all", r"how (?:long|many|much)",
                 r"everything (?:you need to know|we know)", r"what we know", r"lore explained", r"daily (?:quests?|challenges?)",
                 r"weekly (?:reset|vendor)"])

DROP_PATHS = re.compile(r"/(?:guides?|walkthroughs?|deals?|lists?|tips|how-to|best|codes|puzzles?|quiz(?:zes)?|"
                        r"shopping|buying-guides?|wordle|tier-lists?|answers?)(?:/|-|$)", re.IGNORECASE)


def classify(title: str, link: str = "") -> str:
    """Returns 'news', 'review', 'preview' or 'drop'."""
    t = " ".join((title or "").split())
    if RELEASES.search(t) and not re.search(r"(?i)enhance your|flight sticks?|controllers?|headsets?|accessor", t):
        return "news"
    if STRONG_DROP.search(t) or (NUMBERED_LIST.search(t) and not PEOPLE_COUNT.search(t)) or DROP_PATHS.search(link or ""):
        service = re.search(r"(?i)(?<![\w-])(?:guides?|walkthrough|tier list|how to|answers?|wordle|quiz|"
                            r"codes|locations?|should you|best)(?![\w-])", t) or re.search(r"(?i)\b\d+% off|prime day|black friday", t)
        return "news" if EVENT.search(t) and not service else "drop"
    if REVIEW.search(t) and not NOT_REVIEW.search(t):
        return "review"
    if PREVIEW.search(t):
        return "preview"
    if NEWS.search(t):
        return "news"
    if WEAK_DROP.search(t):
        return "drop"
    return "news"
