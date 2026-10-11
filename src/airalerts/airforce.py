"""Air Force (@kpszsu) threat posts as features: how many posts about the unit's oblast in the last hours.

Air Force posts announce drones, missiles and guided bombs heading for an area
("🏍 Реактивні БпЛА на Київщині, курс на н.п.Бородянка"). We count threat posts
per oblast by publish time; a post is usable only after it was published.

The place lexicon (settlement stems -> oblast) and the not-a-signal filter were
built during research, see docs/research/07-telegram-lead-time.md. Matching is
lexical: "з Чернігівщини курсом на Київщину" counts for both oblasts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

UA2EN = {'Івано-Франківська область':'Ivano-Frankivska oblast','Волинська область':'Volynska oblast','Вінницька область':'Vinnytska oblast',
'Дніпропетровська область':'Dnipropetrovska oblast','Донецька область':'Donetska oblast','Житомирська область':'Zhytomyrska oblast',
'Закарпатська область':'Zakarpatska oblast','Запорізька область':'Zaporizka oblast','Київська область':'Kyivska oblast',
'Кіровоградська область':'Kirovohradska oblast','Луганська область':'Luhanska oblast','Львівська область':'Lvivska oblast',
'Миколаївська область':'Mykolaivska oblast','Одеська область':'Odeska oblast','Полтавська область':'Poltavska oblast',
'Рівненська область':'Rivnenska oblast','Сумська область':'Sumska oblast','Тернопільська область':'Ternopilska oblast',
'Харківська область':'Kharkivska oblast','Херсонська область':'Khersonska oblast','Хмельницька область':'Khmelnytska oblast',
'Черкаська область':'Cherkaska oblast','Чернівецька область':'Chernivetska oblast','Чернігівська область':'Chernihivska oblast','м. Київ':'Kyiv City'}
# lexical place -> oblast patterns (lowercase text). Launch-origin names (Донецьк, Луганськ, Крим, російські міста) deliberately NOT mapped.
PLACES = {
'Kyiv City': r'(?<![а-яіїєґ])(київ|києв[аіу])(?![а-яіїєґ])|святошин|троєщин|куренівк|оболон|воскресенк|позняк|осокорк|дарниц|борщагівк|нивки|лівобережн|подол(?!ь)|печерськ|липки|пущ[аіу]-?водиц|жуляни|теремк|видубич|голосіїв|харківськ\w+ масив|русанівк|вигурівщин|бортнич|виноградар|мінськ\w+ масив|лісов\w+ масив|отрадн|відрадн|академмістечк|петрівк|шулявк|лук.янівк|деснянськ|солом.янськ|дніпровськ\w+ район|шевченківськ\w+ район|оболонськ',
'Kyivska oblast': r'київщин|київськ\w+ област|бровар|бориспіл|вишневе|боярк|біла церква|білої церкви|васильків|фастів|обухів|бородянк|буч[аіу](?![а-яіїєґ])|ірпін|гостомел|вишгород|іванків|макарів|димер|тетерів|узин|яготин|переяслав|миронівк|славутич|баришівк|чабани|щасливе|софіївськ\w+ борщагівк|петрівське|калинівк|богуслав|рокитне|тараща|сквир|кагарлик|ржищів|українк[аиу]|тетіїв|бишів|мироцьк|чубинськ|мощун|білогородк|святопетрівськ|крюківщин|коцюбинськ|гнідин|гатне|глеваха|ходосівк|лісники|чайки|вишенк|вишгородськ|пісків|толокунь|наливайківк|ставищ|згурівк|березан|кагарлицьк|стоянк|вороньків|баришівськ|піщан|кодра|ворзель|клавдієв|немішаєв|горенк|забор.я|трипіл|рогозів|гребінк|ясногородк|сухолуччя|тарасівк|прилісн|рославичі|погреби|требухів|літки|княжичі|зазим.я|красилівк|мала дівичка|яготинськ|кропивня|лютіж|дударків|бобриц|мархалівк|сошників|юрів|шпитьки|ірпінськ',
'Chernihivska oblast': r'чернігівщин|чернігів(?![а-яіїєґ])|чернігова|чернігову|чернігові|ніжин|прилук|носівк|сновськ|козелец|бахмач|куликівк|ічн|борзн|талалаївк|городн[яі]|семенівк|новгород-сіверськ|корюківк|щорс|мен[аиіу](?![а-яіїєґ])|кіпті|березн[аи](?![а-яіїєґ])|варв[аи]',
'Sumska oblast': r'сумщин|(?<![а-яіїєґ])суми(?![а-яіїєґ])|сумськ|конотоп|шостк|охтирк|лебедин|ромни|глухів|путивл|краснопілл|білопілл|середина-буд|буринь|тростянец|сум[иі]\b',
'Poltavska oblast': r'полтавщин|полтав|кременчу|лубни|миргород|хорол|горішні плав|комсомольськ|пирятин|лохвиц|гадяч|зіньків|решетилівк|карлівк|лазірк|драбів',
'Kharkivska oblast': r'харківщин|харків(?![а-яіїєґ])|харков|харкова|харкові|чугуїв|куп.янськ|ізюм|балаклі|лозов|богодухів|вовчанськ|мерефа|барвінков|берестин',
'Dnipropetrovska oblast': r'дніпропетровщин|(?<![а-яіїєґ])дніпр[оау](?![а-яіїєґ])|(?<![а-яіїєґ])дніпр[іу](?![а-яіїєґ])|кривий ріг|кривого рогу|павлоград|нікопол|марганец|кам.янськ|синельников|покров(?![а-яіїєґ])|перещепин|самарськ|верхньодніпровськ|жовті води|підгородн|юр.ївк|петриківк',
'Zaporizka oblast': r'запоріжж|запорізьк|мелітопол|бердянськ|енергодар|оріхів|токмак|пологи|василівк|пологівськ',
'Khersonska oblast': r'херсонщин|херсон|каховк|скадовськ|генічеськ',
'Mykolaivska oblast': r'миколаївщин|миколаїв|очаків|вознесенськ|южноукраїнськ|первомайськ|баштанк|нова одеса|снігурівк|кінбурн',
'Odeska oblast': r'одещин|одес[аиіуь]|ізмаїл|чорноморськ|білгород-дністровськ|подільськ|рені(?![а-яіїєґ])|кілі[яї]|татарбунар|арциз|роздільн|затока|лиман(?=.{0,10}одес)|усатове|південн[еийого]+(?= ?\(?одес)|кароліно-бугаз|білярськ|овідіополь|роздільнянськ',
'Kirovohradska oblast': r'кіровоградщин|кропивниц|олександрі[яїю]|знам.янк|світловодськ|гайворон|долинськ|мала виска|новоукраїнк|петрово|олександрівськ',
'Cherkaska oblast': r'черкащин|черкас|умань|золотоніш|канів|(?<![а-яіїєґ])смілі?(?![а-яіїєґ])|звенигородк|ватутіно|ірдин|кам.янк|чигирин|корсунь|жашків|монастирищ',
'Vinnytska oblast': r'вінниччин|вінниц|жмеринк|гайсин|могилів-подільськ|козятин|тульчин|хмільник|ладижин|бар(?![а-яіїєґ])|калинівк|погребищ|немирів|літин',
'Zhytomyrska oblast': r'житомирщин|житомир|коростень|звягел|новоград-волинськ|бердичів|малин(?![а-яіїєґ])|овруч|олевськ|радомишл|коростишів|баранівк|черняхів|попільн|брусилів',
'Khmelnytska oblast': r'хмельниччин|хмельниц|кам.янець-подільськ|шепетівк|старокостянтинів|нетішин|полонн|ізяслав|славут(?![а-яіїєґ]*ич)|красилів|волочиськ|чорний острів|летичів',
'Rivnenska oblast': r'рівненщин|рівне(?![а-яіїєґ])|рівного|рівному|дубно|сарни|вараш|костопіл|острог|здолбунів|радивилів|корець',
'Volynska oblast': r'волин(?![а-яіїєґ]*ськ\w*\s+(?:п|р))|луцьк|ковел|нововолинськ|володимир(?![а-яіїєґ])|ківерц|маневич|любомл|ратн',
'Lvivska oblast': r'львівщин|львів|дрогобич|стрий|червоноград|самбір|трускавец|борислав|сокаль|жовква|броди|яворів|радехів|золочів',
'Ternopilska oblast': r'тернопільщин|тернопіл|кременец|чортків|збараж|бережани|шумськ|теребовл',
'Ivano-Frankivska oblast': r'прикарпат|івано-франківськ|калуш|коломи[яїю]|надвірн|болехів|долин(?![а-яіїєґ]*ськ)|богородчан',
'Zakarpatska oblast': r'закарпат|ужгород|мукачев|берегове|хуст|тячів',
'Chernivetska oblast': r'буковин|чернівц|чернівецьк',
'Donetska oblast': r'донеччин|краматорськ|слов.янськ|дружківк|костянтинівк|покровськ|добропілл|лиман(?![а-яіїєґ]*[^\s]*одес)',
'Luhanska oblast': r'луганщин',
}
PLACES_RE = {k: re.compile(v) for k, v in PLACES.items()}
def oblasts_in(text):
    t = text.lower()
    return sorted(k for k, r in PLACES_RE.items() if r.search(t))

NOT_SIGNAL = re.compile(r'відбій|загальна оцінка|обстановка станом|^\W*мінус|посадка|чисто\b|відсутні|не активна|скасован|⚪|🟢')
CLASSES = [  # ordered: first match wins as the class label
 ('MIG31K',   re.compile(r'м[іиi]г-?\s?31')),
 ('STRAT_AIR',re.compile(r'ту-?\s?95|ту-?\s?160|ту-?\s?22|стратег|зліт|борт[иів]? |у повітрі')),
 ('BALLISTIC',re.compile(r'балісти|іскандер|кинджал|х-?47|кн-?23|циркон|швидкісн')),
 ('CRUISE',   re.compile(r'бандероль|мгкр|калібр|х-?101|х-?59|х-?69|х-?22|онікс|крилат|ракет|ціль на|цілі на')),
 ('LAUNCH',   re.compile(r'пуск|запуск')),
 ('UAV_JET',  re.compile(r'реактив|герань-?[35]')),
 ('UAV',      re.compile(r'бпла|шахед|герань|дрон|ударн|група|групи')),
 ('TRACK',    re.compile(r'\d+\s?[xх](?![а-яіїєґa-z])|курс|напрям|повз|вектор|у бік|далі на|зниження|🅿️|🔄|⚠️|❗')),
]
def classify(text):
    t = text.lower()
    if NOT_SIGNAL.search(t): return None
    for name, r in CLASSES:
        if r.search(t): return name
    return None


EN2UA = {v: k for k, v in UA2EN.items()}
SUMMARY = re.compile(r"збито/подавлено|у ніч на|загалом|обстановка")


def load_threat_posts(path: Path) -> pd.DataFrame:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    df = pd.DataFrame(rows).drop_duplicates("id")
    df["published_at"] = pd.to_datetime(df.published_at, utc=True)
    df = df[~df.edited]  # edit time is unknown: an edited post may contain later information
    recs = []
    for r in df.itertuples():
        t = r.text.lower()
        if SUMMARY.search(t) or classify(r.text) is None:
            continue
        for ob in oblasts_in(r.text):
            recs.append((r.published_at, EN2UA[ob]))
    out = pd.DataFrame(recs, columns=["published_at", "oblast"]).sort_values("published_at")
    return out


class ThreatIndex:
    def __init__(self, posts: pd.DataFrame):
        m = (posts.published_at.dt.as_unit("ns").astype("int64") // 60_000_000_000).to_numpy()
        self.all = np.sort(m)
        self.by_oblast = {o: np.sort(m[(posts.oblast == o).to_numpy()]) for o in posts.oblast.unique()}
        self.start = int(m.min()) if len(m) else None

    def count(self, oblast: str, t: pd.Timestamp, minutes: int) -> float:
        hi = int(pd.Timestamp(t).tz_convert("UTC").value // 60_000_000_000)
        if self.start is None or hi - minutes < self.start:
            return np.nan  # before the scraped history: unknown, not zero
        a = self.by_oblast.get(oblast, np.array([], dtype=np.int64))
        return float(np.searchsorted(a, hi, side="left") - np.searchsorted(a, hi - minutes, side="left"))

    def count_all(self, t: pd.Timestamp, minutes: int) -> float:
        hi = int(pd.Timestamp(t).tz_convert("UTC").value // 60_000_000_000)
        if self.start is None or hi - minutes < self.start:
            return np.nan
        return float(np.searchsorted(self.all, hi, side="left") - np.searchsorted(self.all, hi - minutes, side="left"))
