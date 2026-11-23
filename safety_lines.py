"""
Safety lines: a fixed library of sourced sentences attached by rule.

Some right answers are still unsafe without one more sentence: fish in
pregnancy with nothing on mercury, honey for a cough with nothing on babies
under 1, a statin answer with nothing on stopping. These lines come from
code, never from the model, so the same claim always gets the same line.

Each entry names its hazard, the rule that fires it on the claim's words,
the one sentence the reader sees, and the health-authority page and sentence
that support it (opened on the date in `checked`). A line never changes a
stamp, a citation or what the answer says about the studies: it travels in
the response's own `safety` field, added on the way out
(app._check_response), on every route, for a saved answer and a fresh one
alike, and it is never stored. This module imports nothing from the rest of
the app.

To add an entry: write the line for everyone who asks about the hazard (not
for one claim), open the source page and copy its sentence into `quote`, and
record the page's main heading in `page`, and add a claim it fires on and a
near miss it must stay silent on to the safety block in tests/test_app.py.
"""
import re
from urllib.parse import urlparse

MAX_LINES = 2
CHECKED = "2026-11-23"

_FLAGS = re.IGNORECASE


def _w(*alts: str) -> str:
    """One word group: any of `alts` (regex), whole words only."""
    return r"\b(?:" + "|".join(alts) + r")\b"


# Word groups, each with its everyday synonyms.
PREGNANT = _w(r"pregnan\w*", r"expecting a baby", r"mums? to be", r"moms? to be", r"mothers? to be",
              r"expectant mothers?", r"trying to conceive", r"trying for a baby", r"trimesters?",
              r"antenatal", r"prenatal", r"gestation(?:al)?", r"baby bump")
# Words that say the claim is not about being pregnant: a test for it,
# preventing it, or getting over it.
NOT_PREGNANT = _w(r"pregnancy tests?", r"contracepti\w*", r"birth control", r"prevents? pregnancy",
                  r"preventing pregnancy", r"avoid(?:s|ing)? pregnancy", r"unplanned pregnanc\w*",
                  r"morning after", r"after pregnancy", r"postnatal", r"postpartum")
BABY = _w(r"bab(?:y|ies)(?! aspirin)(?! boomers?)", r"infants?", r"newborns?", r"neonat\w*",
          r"under (?:12|twelve) months", r"under (?:1|one) years?", r"months? old", r"weeks? old")
CHILD = _w(r"child(?:ren)?", r"kids?", r"toddlers?", r"bab(?:y|ies)(?! aspirin)(?! boomers?)",
           r"infants?", r"newborns?", r"p(?:a)?ediatric", r"teen(?:ager)?s?", r"adolescents?",
           r"young people", r"youngsters?", r"school ?children", r"pupils?", r"under (?:5|6|12|16)s?",
           r"(?:boys|girls) and (?:girls|boys)")
BREASTFEEDING = _w(r"breast ?feed\w*", r"breast-feed\w*", r"breastfed", r"nursing mothers?",
                   r"lactat\w*", r"while nursing")
MEDICINE = _w(r"medicines?", r"medications?", r"meds", r"drugs?", r"tablets?", r"pills?", r"capsules?",
              r"painkillers?", r"pain ?relief", r"paracetamol", r"acetaminophen", r"ibuprofen",
              r"aspirin", r"codeine", r"antibiotics?", r"antihistamines?", r"antidepressants?",
              r"supplements?", r"vitamins?", r"multivitamins?", r"minerals?", r"herbal", r"herbs?",
              r"remed(?:y|ies)", r"probiotics?", r"melatonin", r"cbd", r"cannabis", r"homeopath\w*",
              r"aromatherapy", r"essential oils?", r"prescri\w*", r"iron", r"fish oil", r"omega 3s?",
              r"omega-3s?", r"echinacea", r"ginseng", r"turmeric", r"ginger (?:capsules?|supplements?)")
STOPPING = _w(r"stop\w*", r"quit\w*", r"skip\w*", r"com(?:e|ing) off", r"go(?:ing)? off",
              r"giv(?:e|ing) up", r"ditch\w*", r"replac\w*", r"instead of", r"in place of",
              r"rather than", r"swap\w*", r"without", r"no need", r"(?:do(?:es)?n'?t|do(?:es)? not) need",
              r"no longer need\w*", r"not needed", r"unnecessary", r"cut(?:ting)? out", r"wean\w*")
TREATS = _w(r"cur(?:e|es|ed|ing)", r"treat(?:s|ed|ing|ment)?", r"heal(?:s|ed|ing)?", r"reverse[sd]?",
            r"revers(?:es|ing)", r"remission", r"lowers?", r"lowering", r"control(?:s|led|ling)?",
            r"manag(?:e|es|ed|ing)", r"fights?", r"kills?", r"shrinks?", r"beats?", r"get rid of")
REMEDY = _w(r"herbal", r"herbs?", r"natural(?:ly)?", r"remed(?:y|ies)", r"supplements?", r"vitamins?",
            r"homeopath\w*", r"alternative", r"complementary", r"naturopath\w*", r"ayurved\w*",
            r"essential oils?", r"juic(?:e|es|ing)", r"teas?", r"cinnamon", r"turmeric", r"curcumin",
            r"garlic", r"ginger", r"vinegar", r"apple cider", r"cannabis", r"cbd", r"oils?",
            r"diet(?:s|ary)?", r"fasting", r"food(?:s)?", r"acupunctur\w*", r"chiropract\w*", r"reiki",
            r"baking soda", r"apricot kernels?", r"mistletoe", r"spice")
SERIOUS = _w(r"cancers?", r"tumou?rs?", r"diabet\w*", r"hiv", r"aids", r"asthma", r"epilep\w*",
             r"seizures?", r"heart disease", r"heart failure", r"high blood pressure", r"hypertension",
             r"infections?", r"sepsis", r"pneumonia", r"tuberculosis", r"malaria", r"depression",
             r"schizophreni\w*", r"bipolar", r"multiple sclerosis", r"arthritis", r"thyroid")

STATIN = _w(r"statins?", r"atorvastatin", r"simvastatin", r"rosuvastatin", r"pravastatin",
            r"cholesterol lowering (?:medicines?|drugs?|tablets?|pills?)",
            r"cholesterol-lowering (?:medicines?|drugs?|tablets?|pills?)")
ANTIDEPRESSANT = _w(r"antidepressants?", r"anti-depressants?", r"ssris?", r"snris?", r"sertraline",
                    r"fluoxetine", r"citalopram", r"escitalopram", r"paroxetine", r"venlafaxine",
                    r"duloxetine", r"mirtazapine", r"amitriptyline")
AMLODIPINE = _w(r"amlodipine")
WARFARIN = _w(r"warfarin")
ANTICOAGULANT = _w(r"blood thinn\w*", r"anti ?coagulants?", r"apixaban", r"rivaroxaban", r"edoxaban",
                   r"dabigatran", r"doacs?")
EPILEPSY = _w(r"epilep\w*", r"anti ?epileptics?", r"anti-epileptics?", r"anticonvulsants?",
              r"seizure (?:medicines?|medications?|drugs?|tablets?)", r"valproate", r"lamotrigine",
              r"levetiracetam", r"carbamazepine")
DIABETES = _w(r"diabet\w*", r"blood sugar", r"blood glucose", r"hba1c", r"insulin resistance")
DIABETES_MEDICINE = _w(r"metformin", r"insulin(?! resistance)", r"diabetes (?:medicines?|medications?|"
                       r"tablets?|pills?|drugs?|treatment)")
THYROID = _w(r"levothyroxine", r"thyroxine", r"thyroid (?:medicines?|medications?|tablets?|pills?|hormones?)",
             r"underactive thyroid", r"hypothyroid\w*")
INHALER = _w(r"inhalers?", r"preventers?", r"puffers?", r"steroid inhalers?")
ASTHMA = _w(r"asthma\w*")
ANTIBIOTIC = _w(r"antibiotics?", r"amoxicillin", r"penicillin", r"antibiotic course")
IRON = _w(r"iron (?:tablets?|supplements?|pills?|drops?|capsules?|infusions?)", r"ferrous \w+",
          r"taking iron", r"take iron", r"iron for", r"iron treats?", r"iron helps?", r"oral iron")
VITAMIN_A = _w(r"vitamin a(?! (?:day|week|month|night|morning|year))", r"retinol", r"cod liver oil",
               r"fish liver oil", r"beta.carotene")
VITAMIN_D = _w(r"vitamin d\d?", r"vit d", r"cholecalciferol", r"colecalciferol", r"vitamin d3")
GRAPEFRUIT = _w(r"grapefruits?", r"grapefruit juice")
ST_JOHNS = r"\bst\.? ?john'?s ?wort\b|\bhypericum\b"
VITAMIN_K = _w(r"vitamin k\d?", r"vit k", r"green leafy vegetables?", r"leafy greens?", r"kale", r"spinach",
               r"broccoli", r"cranberr\w*")
HONEY = _w(r"honey")
COUGH = _w(r"cough\w*", r"sore throats?", r"colds?")
COUGH_MEDICINE = _w(r"cough (?:and cold )?(?:medicines?|medications?|syrups?|mixtures?|remed(?:y|ies)|drops)",
                    r"cold (?:and flu )?(?:medicines?|medications?|remed(?:y|ies)|tablets?|capsules?)",
                    r"flu (?:medicines?|remed(?:y|ies))", r"over the counter (?:cough|cold)\w*",
                    r"over-the-counter (?:cough|cold)\w*", r"otc (?:cough|cold)\w*", r"cough syrups?",
                    r"cough suppressants?", r"expectorants?", r"dextromethorphan", r"guaifenesin")
DECONGESTANT = _w(r"decongest\w*", r"nasal sprays?", r"nose sprays?", r"nasal drops?", r"nose drops?",
                  r"pseudoephedrine", r"phenylephrine", r"xylometazoline", r"oxymetazoline")
COWS_MILK = _w(r"cows?'? ?milk", r"cow's milk", r"whole milk", r"full fat milk", r"full-fat milk",
               r"dairy milk", r"milk as (?:a|their|the) (?:main )?drink")
WATER = _w(r"water(?! birth)(?!s break)")
SALT_WATER = _w(r"salt water", r"salty water", r"saltwater", r"saline")
SLEEP = _w(r"sleep\w*", r"slept", r"cots?", r"cribs?", r"bed ?shar\w*", r"co ?sleep\w*", r"co-sleep\w*",
           r"sids", r"cot death", r"sudden infant death", r"naps?", r"swaddl\w*", r"moses baskets?")
FEVER = _w(r"fevers?", r"feverish", r"high temperatures?", r"temperatures?", r"febrile")
TUMMY_BUG = _w(r"diarrh\w*", r"vomit\w*", r"being sick", r"dehydrat\w*", r"gastroenteritis",
               r"stomach bugs?", r"tummy bugs?", r"rehydration", r"norovirus", r"rotavirus")
EAR_INFECTION = _w(r"ear infections?", r"earaches?", r"ear aches?", r"otitis(?: media)?", r"glue ear",
                   r"middle ear infections?")
CHICKENPOX = _w(r"chicken ?pox", r"varicella")
NSAID = _w(r"ibuprofen", r"nsaids?", r"anti ?inflammator\w*", r"anti-inflammator\w*", r"naproxen",
           r"painkillers?", r"pain ?relief", r"fever reducers?")
ASPIRIN = _w(r"aspirin")
TEETHING = _w(r"teeth\w*", r"teething gels?")
CANCER = _w(r"cancers?", r"tumou?rs?", r"leuka?emia", r"lymphoma", r"melanoma", r"chemo\w*",
            r"radiotherapy", r"oncolog\w*")
# Words that make a cancer claim one about treating it, not about its causes.
CANCER_TREAT = _w(r"cur(?:e|es|ed|ing)", r"treat(?:s|ed|ing|ment|ments)?", r"heal(?:s|ed|ing)?", r"kills?",
                  r"shrinks?", r"fights?", r"beats?", r"instead of", r"in place of", r"rather than",
                  r"alongside", r"for cancer", r"with cancer", r"cancer patients?", r"people with cancer",
                  r"during chemo\w*", r"alternative (?:medicine|therap(?:y|ies)|treatments?)")
PSA = _w(r"psa", r"prostate specific antigen")
# Words that make "PSA" the prostate test, not an announcement.
PSA_CONTEXT = _w(r"prostate", r"tests?", r"testing", r"levels?", r"results?", r"screening", r"blood",
                 r"raised", r"high", r"elevated", r"scores?", r"readings?", r"antigen")
ALCOHOL = _w(r"alcohol\w*", r"wine", r"beers?", r"spirits", r"booze", r"units? of alcohol",
             r"cocktails?", r"prosecco", r"champagne", r"cider", r"lager", r"vodka", r"whisk(?:e)?y")
CAFFEINE = _w(r"caffein\w*", r"coffees?", r"espressos?", r"lattes?", r"cappuccinos?", r"tea", r"teas",
              r"cola", r"energy drinks?", r"decaf")
HERBAL_TEA = _w(r"herbal teas?", r"herb teas?", r"chamomile", r"camomile", r"peppermint tea",
                r"raspberry leaf", r"liquorice", r"licorice", r"ginger tea", r"nettle tea", r"rooibos",
                r"fruit teas?", r"infusions?")
FISH = _w(r"fish(?! oil)(?! liver)", r"seafood", r"shellfish", r"tuna", r"salmon", r"sushi", r"sashimi",
          r"oily fish", r"swordfish", r"shark", r"marlin", r"oysters?", r"prawns?", r"shrimps?",
          r"mussels?", r"mercury", r"sardines?", r"mackerel")
RAW_FISH = _w(r"raw fish", r"raw shellfish", r"raw seafood", r"sushi", r"sashimi", r"oysters?",
              r"ceviche", r"poke bowls?", r"raw tuna", r"raw salmon")
LIVER = _w(r"liver(?! (?:disease|function|cancer|damage|failure|enzymes?|problems?|tests?))(?! oil)",
           r"haggis")
SOFT_CHEESE = _w(r"unpasteuri[sz]ed", r"raw milk", r"cheeses?", r"brie", r"camembert", r"blue cheese",
                 r"goats? cheese", r"soft cheeses?", r"listeria")
MEAT = _w(r"meat", r"rare steak", r"steaks?", r"salami", r"chorizo", r"pepperoni", r"prosciutto",
          r"parma ham", r"cured meats?", r"deli meats?", r"undercooked", r"toxoplasm\w*", r"raw meat")
EXERCISE = _w(r"exercis\w*", r"sports?", r"running", r"jogging", r"gym", r"workouts?", r"working out",
              r"yoga", r"pilates", r"swimming", r"scuba", r"diving", r"cycling", r"weight ?lifting",
              r"lifting weights", r"physical activity", r"horse riding", r"skiing")
FOLIC = _w(r"folic acid", r"folate", r"neural tube", r"spina bifida")
VITAMIN_A_PREG = VITAMIN_A + r"|" + _w(r"multivitamins?")


# Said to take the place of treatment: in place of it, stopping it, or
# treating a serious condition.
REPLACES_TREATMENT = (_w(r"instead of", r"in place of", r"rather than", r"replac\w*", r"better than",
                         r"no need for", r"(?:do(?:es)?n't|do(?:es)? not) need")
                      + r"|(?:" + STOPPING + r").{0,40}(?:" + MEDICINE + "|" + _w(r"treatments?", r"therap(?:y|ies)",
                                                                                  r"insulin", r"chemo\w*") + r")"
                      + r"|(?:" + TREATS + r").{0,60}(?:" + SERIOUS + r")")


# Each entry. `when`: every pattern must match; `unless`: none may; `over`:
# entries this one makes redundant when both fire; `rank`: 1 is the most
# serious. `url` is the page the line rests on, `page` its main heading, and
# `quote` its own words, opened on the date in CHECKED.
ENTRIES = [
    # ---- danger signs that need care today -------------------------------------
    {"id": "baby-fever", "topic": "A fever in a baby under 3 months", "rank": 1,
     "when": [BABY, FEVER],
     "trigger": "a baby, infant or newborn, and a fever or temperature",
     "line": "A baby under 3 months with a temperature of 38C (100.4F) or higher needs urgent medical advice.",
     "url": "https://www.nhs.uk/symptoms/fever-in-children/",
     "page": "High temperature (fever) in children",
     "quote": "is under 3 months old and has a temperature of 38C or higher, or you think they have a high temperature"},
    {"id": "child-dehydration", "topic": "Dehydration in a young child", "rank": 1,
     "when": [CHILD, TUMMY_BUG],
     "trigger": "a child or baby, and diarrhoea, vomiting, dehydration or rehydration",
     "line": "A child under 5 with diarrhoea or vomiting who shows signs of dehydration, such as fewer "
             "wet nappies (diapers), needs urgent medical advice.",
     "url": "https://www.nhs.uk/symptoms/diarrhoea-and-vomiting/",
     "page": "Diarrhoea and vomiting",
     "quote": "a child under 5 years has signs of dehydration, such as fewer wet nappies"},

    # ---- babies under 12 months ------------------------------------------------
    {"id": "honey-under-1", "topic": "Honey and babies", "rank": 1,
     "when": [HONEY, CHILD + "|" + COUGH],
     "trigger": "honey, with a child or baby, or with a cough, cold or sore throat",
     "line": "Do not give honey to babies under 1 year old.",
     "url": "https://www.nhs.uk/baby/weaning-and-feeding/foods-to-avoid-giving-babies-and-young-children/",
     "page": "Foods to avoid giving babies and young children",
     "quote": "Do not give your child honey until they're over 1 year old."},
    {"id": "baby-safe-sleep", "topic": "How a baby sleeps", "rank": 1,
     "when": [BABY, SLEEP],
     "trigger": "a baby or infant, and sleep, a cot or crib, bed sharing or SIDS",
     "line": "Always put a baby to sleep on their back, with their feet at the bottom of the cot or crib, "
             "to lower the risk of sudden infant death syndrome.",
     "url": "https://www.nhs.uk/baby/caring-for-a-newborn/sudden-infant-death-syndrome-sids/",
     "page": "Sudden infant death syndrome (SIDS)",
     "quote": "always place your baby on their back to sleep in the feet-to-foot position"},
    {"id": "baby-cows-milk", "topic": "Cows' milk for babies", "rank": 2,
     "when": [BABY + "|" + _w(r"first year", r"before 12 months", r"before (?:1|one) year"), COWS_MILK],
     "trigger": "a baby, or the first 12 months, and cows' or whole milk",
     "line": "Cows' milk should not be a baby's main drink until they are 12 months old.",
     "url": "https://www.nhs.uk/baby/weaning-and-feeding/drinks-and-cups-for-babies-and-young-children/",
     "page": "Drinks and cups for babies and young children",
     "quote": "Cows' milk can be introduced as a main drink from 12 months."},
    {"id": "baby-water", "topic": "Water for young babies", "rank": 2,
     "when": [BABY, WATER], "unless": [SALT_WATER],
     "trigger": "a baby or infant, and water (not salt water, a water birth or waters breaking)",
     "line": "Fully breastfed babies do not need water until they start solid foods; formula fed babies "
             "under 6 months may need small sips of cooled boiled water in hot weather.",
     "url": "https://www.nhs.uk/baby/weaning-and-feeding/drinks-and-cups-for-babies-and-young-children/",
     "page": "Drinks and cups for babies and young children",
     "quote": "Fully breastfed babies do not need any water until they've started eating solid foods."},
    {"id": "baby-vitamin-d", "topic": "Vitamin D for babies", "rank": 2,
     "when": [BABY + "|" + _w(r"breastfed", r"breast ?feed\w*", r"formula"), VITAMIN_D],
     "over": ["vitamin-d-limit", "breastfeeding-medicines"],
     "trigger": "a baby, breastfeeding or formula, and vitamin D",
     "line": "Babies from birth to 1 year should have a daily vitamin D supplement, unless they have "
             "more than 500ml (about a pint) of infant formula a day.",
     "url": "https://www.nhs.uk/conditions/vitamins-and-minerals/vitamin-d/",
     "page": "Vitamin D",
     "quote": "all babies (unless they're having more than 500ml of infant formula a day) should take a daily "
              "supplement throughout the year"},

    # ---- children ----------------------------------------------------------------
    {"id": "child-iron-overdose", "topic": "Iron tablets and children", "rank": 1,
     "when": [IRON],
     "trigger": "iron tablets, supplements or drops, or taking iron",
     "line": "Keep iron tablets out of sight and reach of children, as an overdose may be fatal.",
     "url": "https://www.nhs.uk/medicines/ferrous-sulfate/how-and-when-to-take-ferrous-sulfate/",
     "page": "How and when to take ferrous sulfate",
     "quote": "Keep ferrous sulfate out of sight and reach of children, as an overdose may be fatal."},
    {"id": "child-aspirin", "topic": "Aspirin and children", "rank": 1,
     "when": [ASPIRIN, CHILD + "|" + _w(r"reye'?s", r"under 16")],
     "trigger": "aspirin with a child, teenager or baby (\"baby aspirin\" does not count)",
     "line": "Do not give aspirin to children aged 15 or under: in rare cases it can cause a serious "
             "condition called Reye's syndrome.",
     "url": "https://www.nhs.uk/medicines/aspirin/",
     "page": "Aspirin",
     "quote": "Do not give aspirin to children aged 15 years or under because it can cause a serious "
              "condition called Reye's syndrome in rare cases."},
    {"id": "child-cough-cold-medicine", "topic": "Cough and cold medicines for young children", "rank": 1,
     "when": [CHILD, COUGH_MEDICINE],
     "trigger": "a child or baby, and a cough or cold medicine or syrup",
     "line": "The FDA does not recommend over the counter cough and cold medicines for children under 2, "
             "and their labels say not to use them under 4.",
     "url": "https://www.fda.gov/consumers/consumer-updates/should-you-give-kids-medicine-coughs-and-colds",
     "page": "Should You Give Kids Medicine for Coughs and Colds?",
     "quote": "The FDA doesn't recommend OTC medicines for cough and cold symptoms in children younger than 2 "
              "... \"Do not use in children under 4 years of age.\""},
    {"id": "child-decongestant", "topic": "Decongestants for children", "rank": 2,
     "when": [CHILD, DECONGESTANT], "unless": [EAR_INFECTION], "over": ["child-cough-cold-medicine"],
     "trigger": "a child or baby, and a decongestant or nasal spray (not with an ear infection)",
     "line": "Not all decongestants are suitable for children: check the leaflet before giving one to "
             "a child, and ask a pharmacist if you are not sure.",
     "url": "https://www.nhs.uk/medicines/decongestants/",
     "page": "Decongestants",
     "quote": "Check the leaflet that comes with the medicine before giving a decongestant to a child"},
    {"id": "child-ibuprofen-chickenpox", "topic": "Ibuprofen and chickenpox", "rank": 1,
     "when": [CHICKENPOX, NSAID],
     "trigger": "chickenpox, and ibuprofen, anti-inflammatories or painkillers",
     "line": "Do not use ibuprofen for chickenpox unless a doctor tells you to, as it may cause serious "
             "skin infections.",
     "url": "https://www.nhs.uk/conditions/chickenpox/",
     "page": "Chickenpox",
     "quote": "do not use ibuprofen unless you're told to by a doctor, as it may cause serious skin infections"},
    {"id": "child-teething-gel", "topic": "Teething gels", "rank": 2,
     "when": [_w(r"teething")],
     "trigger": "teething",
     "line": "For a teething baby, use only a gel made for young children: general oral pain relief "
             "gels are not suitable for children.",
     "url": "https://www.nhs.uk/baby/babys-development/teething/tips-for-helping-your-teething-baby/",
     "page": "Tips for helping your teething baby",
     "quote": "General oral pain relief gels are not suitable for children."},
    {"id": "child-salt-gargle", "topic": "Gargling salt water", "rank": 2,
     "when": [_w(r"gargl\w*"), SALT_WATER + "|" + _w(r"salt")],
     "trigger": "gargling, with salt or salt water",
     "line": "Gargling with warm salty water is not for children: they should not try it.",
     "url": "https://www.nhs.uk/symptoms/sore-throat/",
     "page": "Sore throat",
     "quote": "gargle with warm, salty water (children should not try this)"},
    {"id": "ear-infection-decongestant", "topic": "Decongestants for an ear infection", "rank": 2,
     "when": [EAR_INFECTION, DECONGESTANT + "|" + _w(r"antihistamines?")],
     "over": ["child-decongestant"],
     "trigger": "an ear infection or earache, and a decongestant, nasal spray or antihistamine",
     "line": "For an ear infection, do not use decongestants or antihistamines: there is no evidence they help.",
     "url": "https://www.nhs.uk/conditions/ear-infections/",
     "page": "Ear infections",
     "quote": "do not use decongestants or antihistamines – there's no evidence they help with ear infections"},
    {"id": "child-ear-infection", "topic": "Ear infections in children", "rank": 2,
     "when": [CHILD, EAR_INFECTION],
     "trigger": "a child or baby, and an ear infection or earache",
     "line": "A baby under 12 months who may have an ear infection should see a doctor; for an older child, "
             "a pharmacist can help first.",
     "url": "https://www.nhs.uk/conditions/ear-infections/",
     "page": "Ear infections",
     "quote": "your child is under 12 months old and you think they have an ear infection"},

    # ---- pregnancy -----------------------------------------------------------------
    {"id": "pregnancy-stop-medicine", "topic": "Stopping a prescribed medicine in pregnancy", "rank": 1,
     "when": [PREGNANT, MEDICINE, STOPPING], "unless": [NOT_PREGNANT],
     "over": ["pregnancy-medicines", "stop-in-place-of-treatment"],
     "trigger": "pregnancy, a medicine, and stopping, skipping or doing without it",
     "line": "In pregnancy, never stop a medicine prescribed to keep you healthy without talking to "
             "your doctor first: stopping could harm you and your baby.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/medicines/",
     "page": "Medicines in pregnancy",
     "quote": "Stopping taking your medicine could be harmful to both you and your baby."},
    {"id": "pregnancy-epilepsy", "topic": "Epilepsy medicine in pregnancy", "rank": 1,
     "when": [PREGNANT, EPILEPSY], "unless": [NOT_PREGNANT],
     "over": ["epilepsy-medicine", "pregnancy-medicines", "pregnancy-stop-medicine"],
     "trigger": "pregnancy and epilepsy or an epilepsy medicine",
     "line": "If you take epilepsy medicine and are pregnant, keep taking it, and speak to your "
             "specialist team straight away.",
     "url": "https://www.nhs.uk/conditions/epilepsy/",
     "page": "Epilepsy",
     "quote": "keep taking your medicine but speak to your specialist team straight away"},
    {"id": "pregnancy-alcohol", "topic": "Alcohol in pregnancy", "rank": 1,
     "when": [PREGNANT, ALCOHOL], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy or trying for a baby, and alcohol, wine, beer or drinking",
     "line": "If you are pregnant or planning a pregnancy, the advice is not to drink alcohol.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/drinking-alcohol-while-pregnant/",
     "page": "Drinking alcohol while pregnant",
     "quote": "It's recommended that if you're pregnant or planning to become pregnant you should not drink alcohol."},
    {"id": "pregnancy-vitamin-a", "topic": "Vitamin A supplements in pregnancy", "rank": 1,
     "when": [PREGNANT, VITAMIN_A_PREG], "unless": [NOT_PREGNANT],
     "over": ["vitamin-a-limit", "pregnancy-medicines"],
     "trigger": "pregnancy, and vitamin A, retinol, cod or fish liver oil, or multivitamins",
     "line": "In pregnancy, do not take cod liver oil or supplements containing vitamin A, sometimes "
             "called retinol.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/pregnancy-vitamins-and-supplements/",
     "page": "Pregnancy vitamins and supplements",
     "quote": "Do not take cod liver oil or supplements containing vitamin A (sometimes called retinol) "
              "when you're pregnant."},
    {"id": "pregnancy-liver", "topic": "Liver in pregnancy", "rank": 1,
     "when": [PREGNANT, LIVER], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and liver or liver products (not liver disease or liver tests)",
     "line": "In pregnancy, avoid liver and liver products: they are high in vitamin A, which can be "
             "harmful to your baby.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "Liver and foods containing liver have high levels of vitamin A, which can be harmful to your baby."},
    {"id": "pregnancy-caffeine", "topic": "Caffeine in pregnancy", "rank": 2,
     "when": [PREGNANT, CAFFEINE], "unless": [NOT_PREGNANT, HERBAL_TEA],
     "trigger": "pregnancy, and caffeine, coffee, tea, cola or energy drinks (not herbal tea)",
     "line": "In pregnancy, have no more than 200mg of caffeine a day: it is in coffee, tea, cola, "
             "energy drinks and chocolate.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "you should not have more than 200mg of caffeine a day"},
    {"id": "pregnancy-raw-fish", "topic": "Raw fish and shellfish in pregnancy", "rank": 2,
     "when": [PREGNANT, RAW_FISH], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and raw fish or shellfish, sushi, sashimi or oysters",
     "line": "In pregnancy, avoid raw fish and raw shellfish, including sushi made with raw fish, even if it "
             "has been frozen.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "sushi made with raw fish (even if it has been previously frozen) or raw shellfish"},
    {"id": "pregnancy-fish", "topic": "Fish in pregnancy", "rank": 2,
     "when": [PREGNANT, FISH], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and fish, seafood, shellfish, tuna, sushi or mercury (not fish oil)",
     "line": "In pregnancy, avoid shark, swordfish and marlin, and eat no more than 2 portions of oily fish "
             "and 4 cans of tuna a week.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "tuna – you should not eat more than 4 cans or 2 tuna steaks a week"},
    {"id": "pregnancy-soft-cheese", "topic": "Unpasteurised dairy in pregnancy", "rank": 2,
     "when": [PREGNANT, SOFT_CHEESE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and cheese, unpasteurised or raw milk, or listeria",
     "line": "In pregnancy, avoid unpasteurised milk and some soft cheeses, which may contain listeria "
             "bacteria.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "Unpasteurised or soft ripened dairy foods may contain listeria bacteria, which can cause listeriosis."},
    {"id": "pregnancy-raw-meat", "topic": "Raw and cured meat in pregnancy", "rank": 2,
     "when": [PREGNANT, MEAT], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and meat, steak, cured or deli meats, or toxoplasmosis",
     "line": "In pregnancy, avoid raw, undercooked or cured meat: it carries a small risk of "
             "toxoplasmosis, which can cause miscarriage.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "There's a small risk of getting toxoplasmosis if you eat raw, undercooked or cured meat, "
              "which can cause miscarriage."},
    {"id": "pregnancy-herbal-tea", "topic": "Herbal tea in pregnancy", "rank": 2,
     "when": [PREGNANT, HERBAL_TEA], "unless": [NOT_PREGNANT], "over": ["pregnancy-medicines"],
     "trigger": "pregnancy, and herbal, fruit or liquorice tea",
     "line": "In pregnancy, drink no more than 4 cups of herbal tea a day, and avoid teas containing "
             "liquorice root.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/",
     "page": "Foods to avoid in pregnancy",
     "quote": "avoid drinking more than 4 cups of herbal tea a day, and avoid teas containing liquorice root"},
    {"id": "pregnancy-exercise", "topic": "Exercise in pregnancy", "rank": 2,
     "when": [PREGNANT, EXERCISE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and exercise, sport, the gym, swimming, diving or being active",
     "line": "In pregnancy, do not scuba dive, and avoid sports where there is a risk of falling or "
             "bumping your tummy.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/exercise/",
     "page": "Exercise in pregnancy",
     "quote": "do not do sports or activities where there's a risk of falling or bumping your tummy, "
              "like hockey, rugby or mountain biking"},
    {"id": "pregnancy-folic-acid", "topic": "Folic acid before and in pregnancy", "rank": 3,
     "when": [PREGNANT + "|" + _w(r"neural tube", r"spina bifida", r"conceiv\w*", r"conception"), FOLIC], "unless": [NOT_PREGNANT], "over": ["pregnancy-medicines"],
     "trigger": "folic acid or folate, with pregnancy, conceiving, neural tube defects or spina bifida",
     "line": "Take 400 micrograms of folic acid a day while trying to get pregnant and for the first "
             "12 weeks of pregnancy.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/pregnancy-vitamins-and-supplements/",
     "page": "Pregnancy vitamins and supplements",
     "quote": "Take folic acid for the first 12 weeks of pregnancy."},
    {"id": "pregnancy-medicines", "topic": "Medicines, supplements and remedies in pregnancy", "rank": 2,
     "when": [PREGNANT, MEDICINE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and any medicine, supplement, vitamin or herbal remedy",
     "line": "If you are pregnant, check with your doctor, midwife or pharmacist before taking any "
             "medicine, and tell them about any herbal remedies.",
     "url": "https://www.nhs.uk/pregnancy/keeping-well/medicines/",
     "page": "Medicines in pregnancy",
     "quote": "Tell your midwife, doctor or pharmacist if you're using herbal, homeopathic or aromatherapy "
              "remedies or therapies."},

    # ---- breastfeeding -----------------------------------------------------------
    {"id": "breastfeeding-medicines", "topic": "Medicines while breastfeeding", "rank": 2,
     "when": [BREASTFEEDING, MEDICINE],
     "trigger": "breastfeeding, and any medicine, supplement or herbal remedy",
     "line": "If you are breastfeeding, check with a doctor, midwife or pharmacist before taking a medicine "
             "or herbal remedy.",
     "url": "https://www.nhs.uk/baby/breastfeeding-and-bottle-feeding/breastfeeding-and-lifestyle/medicines/",
     "page": "Breastfeeding and medicines",
     "quote": "Always check with a GP, your midwife, health visitor or a pharmacist, who can advise you."},
    {"id": "breastfeeding-alcohol", "topic": "Alcohol while breastfeeding", "rank": 2,
     "when": [BREASTFEEDING, ALCOHOL],
     "trigger": "breastfeeding, and alcohol, wine or beer",
     "line": "If you are breastfeeding, the safest approach is to avoid alcohol; an occasional drink is "
             "unlikely to harm your baby if you wait at least 2 hours before feeding.",
     "url": "https://www.nhs.uk/baby/breastfeeding-and-bottle-feeding/breastfeeding-and-lifestyle/alcohol/",
     "page": "Breastfeeding and drinking alcohol",
     "quote": "for the safest approach, you should avoid alcohol completely when breastfeeding. But an "
              "occasional drink is unlikely to harm your baby"},

    # ---- prescribed medicines a reader might stop or swap ------------------------
    {"id": "statins", "topic": "Statins", "rank": 1, "when": [STATIN],
     "trigger": "statins, by name or as cholesterol lowering medicine",
     "line": "If you take a statin, talk to a doctor or pharmacist before stopping it.",
     "url": "https://www.nhs.uk/medicines/statins/",
     "page": "Statins",
     "quote": "Talk to a doctor or pharmacist before stopping."},
    {"id": "antidepressants", "topic": "Antidepressants", "rank": 1, "when": [ANTIDEPRESSANT],
     "trigger": "antidepressants, by class or name",
     "line": "Do not stop taking an antidepressant suddenly or without talking to your doctor first.",
     "url": "https://www.nhs.uk/medicines/antidepressants/",
     "page": "Antidepressants",
     "quote": "Do not stop taking an antidepressant suddenly or without talking to your doctor first."},
    {"id": "blood-pressure-medicine", "topic": "Amlodipine", "rank": 1, "when": [AMLODIPINE],
     "trigger": "amlodipine",
     "line": "If you take amlodipine, do not stop it unless a doctor tells you to: stopping suddenly can "
             "be dangerous.",
     "url": "https://www.nhs.uk/medicines/amlodipine/",
     "page": "Amlodipine",
     "quote": "Do not stop taking amlodipine unless a doctor tells you to. Stopping suddenly can be dangerous."},
    {"id": "warfarin", "topic": "Warfarin", "rank": 1, "when": [WARFARIN],
     "trigger": "warfarin",
     "line": "If you take warfarin, do not stop it unless your doctor tells you to, and tell them about "
             "any herbal remedy, vitamin or supplement.",
     "url": "https://www.nhs.uk/medicines/warfarin/",
     "page": "Warfarin",
     "quote": "Do not stop taking warfarin unless your doctor tells you to."},
    {"id": "blood-thinners", "topic": "Blood thinners (anticoagulants)", "rank": 1, "when": [ANTICOAGULANT],
     "trigger": "blood thinners, anticoagulants, or apixaban, rivaroxaban, edoxaban or dabigatran",
     "line": "If you take a blood thinner (anticoagulant), speak to a doctor or pharmacist before taking any "
             "other medicine, as some can affect how it works.",
     "url": "https://www.nhs.uk/medicines/anticoagulants/",
     "page": "Anticoagulant medicines",
     "quote": "Speak to your GP, anticoagulant clinic or pharmacist before taking any other medicines, including "
              "prescription and over-the-counter medicines"},
    {"id": "epilepsy-medicine", "topic": "Epilepsy medicines", "rank": 1, "when": [EPILEPSY],
     "trigger": "epilepsy, anticonvulsants or an epilepsy medicine by name",
     "line": "If you take epilepsy medicine, do not change the dose or stop it without talking to "
             "your specialist.",
     "url": "https://www.nhs.uk/conditions/epilepsy/",
     "page": "Epilepsy",
     "quote": "Do not change your dose or stop taking your medicine without talking to your specialist."},
    {"id": "diabetes-medicine", "topic": "Diabetes medicines", "rank": 1,
     "when": [DIABETES_MEDICINE + "|" + DIABETES, DIABETES_MEDICINE + "|" + TREATS + "|" + REMEDY], "over": ["stop-in-place-of-treatment"],
     "trigger": "a diabetes medicine by name, or diabetes or blood sugar with a treatment, remedy or food",
     "line": "If you take medicine for diabetes, keep taking it as prescribed: if you stop, your blood "
             "sugar may start to rise again.",
     "url": "https://www.nhs.uk/medicines/metformin/",
     "page": "Metformin",
     "quote": "It's important to keep taking your medicine. If you stop, your blood sugar level may start to rise again."},
    {"id": "thyroid-medicine", "topic": "Thyroid medicine", "rank": 2, "when": [THYROID],
     "trigger": "levothyroxine, thyroid medicine or an underactive thyroid",
     "line": "If you take levothyroxine, keep taking it unless you are advised to stop; some "
             "supplements, such as iron and calcium, may not mix well with it.",
     "url": "https://www.nhs.uk/medicines/levothyroxine/",
     "page": "Levothyroxine",
     "quote": "Keep taking your medicine unless you're advised to stop."},
    {"id": "asthma-inhaler", "topic": "Asthma inhalers", "rank": 1,
     "when": [INHALER + "|" + ASTHMA, INHALER + "|" + STOPPING + "|" + TREATS + "|" + REMEDY + "|" + MEDICINE],
     "trigger": "an inhaler, or asthma with a treatment, a remedy or doing without",
     "line": "Use asthma inhalers as your care team recommends: a preventer inhaler is for every day, "
             "to help prevent symptoms.",
     "url": "https://www.nhs.uk/conditions/asthma/",
     "page": "Asthma",
     "quote": "a preventer inhaler to use every day to help prevent symptoms"},
    {"id": "antibiotics", "topic": "Antibiotics", "rank": 3, "when": [ANTIBIOTIC],
     "trigger": "antibiotics",
     "line": "If you are prescribed antibiotics, take them as directed on the packet or the leaflet "
             "that comes with them.",
     "url": "https://www.nhs.uk/medicines/antibiotics/",
     "page": "Antibiotics",
     "quote": "Take antibiotics as directed on the packet or the patient information leaflet that comes "
              "with the medicine."},

    # ---- supplements and foods with known interactions or overdose risks ---------
    {"id": "st-johns-wort", "topic": "St John's wort", "rank": 1, "when": [ST_JOHNS],
     "trigger": "St John's wort",
     "line": "Taking St John's wort with other medicines, such as anticonvulsants, anticoagulants, "
             "antidepressants or the contraceptive pill, can cause serious problems.",
     "url": "https://www.nhs.uk/mental-health/conditions/depression-in-adults/treatment/",
     "page": "Depression in adults: treatment",
     "quote": "Taking St John's wort with other medicines, such as anticonvulsants, anticoagulants, "
              "antidepressants and the contraceptive pill, can also cause serious problems."},
    {"id": "grapefruit", "topic": "Grapefruit and medicines", "rank": 2, "when": [GRAPEFRUIT],
     "trigger": "grapefruit or grapefruit juice",
     "line": "Grapefruit and grapefruit juice can affect how some medicines work; ask a doctor or "
             "pharmacist whether it interacts with yours.",
     "url": "https://www.fda.gov/consumers/consumer-updates/grapefruit-juice-and-some-drugs-dont-mix",
     "page": "Grapefruit Juice and Some Drugs Don't Mix",
     "quote": "Grapefruit juice and grapefruit can affect the way your medicines work, and that food and "
              "drug interaction can be a concern."},
    {"id": "warfarin-vitamin-k", "topic": "Vitamin K foods and warfarin", "rank": 2,
     "when": [_w(r"vitamin k\d?", r"vit k") + "|" + WARFARIN,
              VITAMIN_K + "|" + _w(r"diet", r"foods?", r"eat\w*", r"vegetables?", r"greens")],
     "trigger": "vitamin K, or warfarin with food, diet, greens or cranberry",
     "line": "If you take warfarin, foods high in vitamin K, such as green leafy vegetables, can affect how "
             "it works: you can still eat them, but talk to your warfarin clinic.",
     "url": "https://www.nhs.uk/medicines/warfarin/",
     "page": "Warfarin",
     "quote": "Foods high in vitamin K ... can affect how warfarin works. You can still eat these foods, but "
              "talk to your warfarin clinic for advice."},
    {"id": "vitamin-a-limit", "topic": "Too much vitamin A", "rank": 2, "when": [VITAMIN_A],
     "unless": [_w(r"creams?", r"serums?", r"topical", r"skin ?care", r"wrinkles?", r"acne", r"face")],
     "trigger": "vitamin A, retinol or fish liver oil taken by mouth (not skin creams)",
     "line": "Having more than 1.5mg of vitamin A a day over many years may affect your bones; if you "
             "eat liver every week, do not take vitamin A supplements.",
     "url": "https://www.nhs.uk/conditions/vitamins-and-minerals/vitamin-a/",
     "page": "Vitamin A",
     "quote": "having more than an average of 1.5 mg (1,500 µg) a day of vitamin A over many years may affect your bones"},
    {"id": "vitamin-d-limit", "topic": "Too much vitamin D", "rank": 2, "when": [VITAMIN_D],
     "trigger": "vitamin D",
     "line": "Adults should not take more than 100 micrograms (4,000 IU) of vitamin D a day, as it "
             "could be harmful.",
     "url": "https://www.nhs.uk/conditions/vitamins-and-minerals/vitamin-d/",
     "page": "Vitamin D",
     "quote": "Do not take more than 100 micrograms (4,000 IU) of vitamin D a day as it could be harmful."},

    # ---- a remedy offered in place of treatment ----------------------------------
    {"id": "cancer-remedies", "topic": "Remedies alongside cancer treatment", "rank": 1,
     "when": [CANCER, CANCER_TREAT], "over": ["stop-in-place-of-treatment"],
     "trigger": "cancer or chemotherapy, with a cure, treatment, remedy, supplement or alternative",
     "line": "If you have cancer, talk to your care team about any remedy or supplement you use, so "
             "they can check it works well with your treatment.",
     "url": "https://medlineplus.gov/canceralternativetherapies.html",
     "page": "Cancer Alternative Therapies",
     "quote": "Your provider can help you make sure that everything you're using for your cancer care works well together."},
    {"id": "stop-in-place-of-treatment", "topic": "A remedy in place of prescribed treatment", "rank": 1,
     "when": [REMEDY + "|" + _w(r"lifestyle", r"therap(?:y|ies)"), REPLACES_TREATMENT],
     "unless": [_w(r"(?:hormone|nicotine|joint|hip|knee|meal) replacement\w*", r"replacement therap(?:y|ies)")],
     "trigger": "a remedy, supplement, food or therapy said to replace treatment, or to treat a serious "
                "condition such as cancer, diabetes or HIV",
     "line": "If you take a prescribed medicine, do not stop it without first talking to your doctor, "
             "whatever else you try.",
     "url": "https://www.nhs.uk/tests-and-treatments/herbal-medicines-and-complementary-therapies/",
     "page": "Herbal medicines and complementary therapies",
     "quote": "do not stop taking any prescribed medicine without first talking to your doctor"},

    # ---- a test result read at home ---------------------------------------------
    {"id": "psa-result", "topic": "A raised PSA result", "rank": 3, "when": [PSA, PSA_CONTEXT],
     "trigger": "a PSA or prostate specific antigen test, level or result (not PSA meaning an announcement)",
     "line": "A high PSA does not mean you have cancer; if your level is raised, you may be offered another "
             "PSA test to check it.",
     "url": "https://www.nhs.uk/tests-and-treatments/psa-test/",
     "page": "PSA test",
     "quote": "A high PSA does not mean you have cancer."},
]

# The health authority behind each allowed host, as the reader sees it.
ORGS = {
    "nhs.uk": "NHS",
    "fda.gov": "FDA",
    "medlineplus.gov": "MedlinePlus",
    "cdc.gov": "CDC",
    "who.int": "WHO",
    "nice.org.uk": "NICE",
    "cochranelibrary.com": "Cochrane",
}


def org_for(url: str) -> str | None:
    """The organisation for an allowed host, or None for any other host."""
    host = urlparse(url).hostname or ""
    return next((name for h, name in ORGS.items() if host == h or host.endswith("." + h)), None)


for _e in ENTRIES:
    _e["checked"] = CHECKED
    _e["org"] = org_for(_e["url"])
    _e["_when"] = [re.compile(p, _FLAGS) for p in _e["when"]]
    _e["_unless"] = [re.compile(p, _FLAGS) for p in _e.get("unless", ())]
BY_ID = {e["id"]: e for e in ENTRIES}
LINES = frozenset(e["line"] for e in ENTRIES)


def normalise(claim: str) -> str:
    """The claim as the rules read it: straight quotes, hyphens as spaces
    between words, one space, lower case."""
    text = str(claim or "").replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"(?<=\w)[-\u2010\u2011](?=\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _fires(entry: dict, text: str) -> bool:
    return (all(p.search(text) for p in entry["_when"])
            and not any(p.search(text) for p in entry["_unless"]))


def match(claim: str) -> list[dict]:
    """The entries for this claim, most serious first, at most MAX_LINES.
    A sourced line is given whenever its rule fires, even if the answer
    says something like it already: a caution said twice costs less than
    one that is missing."""
    text = normalise(claim)
    hits = [e for e in ENTRIES if _fires(e, text)]
    covered = {o for e in hits for o in e.get("over", ())}
    hits = [e for e in hits if e["id"] not in covered]
    hits.sort(key=lambda e: e["rank"])  # stable: library order breaks ties
    return hits[:MAX_LINES]


def lines_for(claim: str) -> list[str]:
    return [e["line"] for e in match(claim)]


def safety(claim: str) -> list[dict]:
    """
    The response's `safety` field: 1 or 2 items, most serious first, each
    the line and where it comes from. Empty when no rule fires, and then
    the response carries no `safety` key at all.
    """
    return [{"line": e["line"], "org": e["org"], "page": e["page"], "url": e["url"]}
            for e in match(claim)]
