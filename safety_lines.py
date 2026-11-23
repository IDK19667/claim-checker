"""
Safety lines: a fixed library of sourced sentences attached by rule.

Some right answers are still unsafe without one more sentence: fish in
pregnancy with nothing on mercury, honey for a cough with nothing on babies
under 1, a statin answer with nothing on stopping. These lines come from
code, never from the model, so the same claim always gets the same line.

Each entry names its hazard, the rule that fires it on the claim's words,
the one sentence the reader sees, and the health-authority page and sentence
that support it (opened on the date in `checked`). A line never changes a
stamp, a citation or what the answer says about the studies: it is added to
the reasoning on the way out (app._check_response), on every route, for a
saved answer and a fresh one alike, and it is never stored.

To add an entry: write the line for everyone who asks about the hazard (not
for one claim), open the source page and copy its sentence into `quote`, and
add a claim it fires on and a near miss it must stay silent on to the safety
block in tests/test_app.py.
"""
import re

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
BP_MEDICINE = _w(r"blood pressure (?:medicines?|medications?|tablets?|pills?|drugs?|treatment)",
                 r"antihypertensives?", r"amlodipine", r"ramipril", r"lisinopril", r"losartan",
                 r"candesartan", r"bisoprolol", r"ace inhibitors?", r"beta ?blockers?", r"beta-blockers?",
                 r"calcium channel blockers?", r"diuretics?", r"water pills?")
WARFARIN = _w(r"warfarin", r"blood thinn\w*", r"blood-thinn\w*", r"anticoagulants?", r"apixaban",
              r"rivaroxaban", r"edoxaban", r"dabigatran")
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
ADULT_ASPIRIN = _w(r"baby aspirin", r"low ?dose aspirin", r"low-dose aspirin", r"daily aspirin",
                   r"aspirin a day")
TEETHING = _w(r"teeth\w*", r"teething gels?")
CANCER = _w(r"cancers?", r"tumou?rs?", r"leuka?emia", r"lymphoma", r"melanoma", r"chemo\w*",
            r"radiotherapy", r"oncolog\w*")
# Words that make a cancer claim one about treating it, not about its causes.
CANCER_TREAT = _w(r"cur(?:e|es|ed|ing)", r"treat(?:s|ed|ing|ment|ments)?", r"heal(?:s|ed|ing)?", r"kills?",
                  r"shrinks?", r"fights?", r"beats?", r"instead of", r"in place of", r"rather than",
                  r"alongside", r"for cancer", r"with cancer", r"cancer patients?", r"people with cancer",
                  r"during chemo\w*", r"alternative (?:medicine|therap(?:y|ies)|treatments?)")
PSA = _w(r"psa", r"prostate specific antigen", r"prostate-specific antigen")
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
# entries this one makes redundant when both fire; `hazard`: entries with the
# same hazard as an existing guard sentence stay silent when that sentence
# is already on the answer. `rank`: 1 is the most serious.
ENTRIES = [
    # ---- danger signs that need care today -------------------------------------
    {"id": "baby-fever", "topic": "A fever in a baby under 3 months", "rank": 1,
     "when": [BABY, FEVER],
     "trigger": "a baby, infant or newborn, and a fever or temperature",
     "line": "A baby under 3 months with a temperature of 38C or higher needs urgent medical advice.",
     "source": ("NHS: Fever in children", "https://www.nhs.uk/conditions/fever-in-children/"),
     "quote": "is under 3 months old and has a temperature of 38C or higher, or you think they have a high temperature"},
    {"id": "child-dehydration", "topic": "Dehydration in a young child", "rank": 1,
     "when": [CHILD, TUMMY_BUG],
     "trigger": "a child or baby, and diarrhoea, vomiting, dehydration or rehydration",
     "line": "A child under 5 with diarrhoea or vomiting who shows signs of dehydration, such as fewer "
             "wet nappies, needs urgent medical advice.",
     "source": ("NHS: Diarrhoea and vomiting", "https://www.nhs.uk/conditions/diarrhoea-and-vomiting/"),
     "quote": "a child under 5 years has signs of dehydration, such as fewer wet nappies"},

    # ---- babies under 12 months ------------------------------------------------
    {"id": "honey-under-1", "topic": "Honey and babies", "rank": 1,
     "when": [HONEY, CHILD + "|" + COUGH],
     "trigger": "honey, with a child or baby, or with a cough, cold or sore throat",
     "line": "Do not give honey to babies under 1 year old.",
     "source": ("NHS: Foods to avoid giving babies and young children",
                "https://www.nhs.uk/conditions/baby/weaning-and-feeding/foods-to-avoid-giving-babies-and-young-children/"),
     "quote": "Do not give your child honey until they're over 1 year old."},
    {"id": "baby-safe-sleep", "topic": "How a baby sleeps", "rank": 1,
     "when": [BABY, SLEEP],
     "trigger": "a baby or infant, and sleep, a cot, bed sharing or SIDS",
     "line": "Always put a baby to sleep on their back, with their feet at the foot of the cot, "
             "to lower the risk of sudden infant death syndrome.",
     "source": ("NHS: Reduce the risk of sudden infant death syndrome",
                "https://www.nhs.uk/baby/caring-for-a-newborn/reduce-the-risk-of-sudden-infant-death-syndrome/"),
     "quote": "always place your baby on their back to sleep"},
    {"id": "baby-cows-milk", "topic": "Cows' milk for babies", "rank": 2,
     "when": [BABY + "|" + _w(r"first year", r"before 12 months", r"before (?:1|one) year"), COWS_MILK],
     "trigger": "a baby, or the first 12 months, and cows' or whole milk",
     "line": "Cows' milk should not be a baby's main drink until they are 12 months old.",
     "source": ("NHS: Drinks and cups for babies and young children",
                "https://www.nhs.uk/conditions/baby/weaning-and-feeding/drinks-and-cups-for-babies-and-young-children/"),
     "quote": "Cows' milk should not be given as a main drink to babies until they're 12 months old"},
    {"id": "baby-water", "topic": "Water for young babies", "rank": 2,
     "when": [BABY, WATER], "unless": [SALT_WATER],
     "trigger": "a baby or infant, and water (not salt water, a water birth or waters breaking)",
     "line": "Fully breastfed babies do not need water until they start solid foods; formula fed babies "
             "under 6 months may need small sips of cooled boiled water in hot weather.",
     "source": ("NHS: Drinks and cups for babies and young children",
                "https://www.nhs.uk/conditions/baby/weaning-and-feeding/drinks-and-cups-for-babies-and-young-children/"),
     "quote": "Fully breastfed babies do not need any water until they've started eating solid foods."},
    {"id": "baby-vitamin-d", "topic": "Vitamin D for babies", "rank": 2,
     "when": [BABY + "|" + _w(r"breastfed", r"breast ?feed\w*", r"formula"), VITAMIN_D],
     "over": ["vitamin-d-limit", "breastfeeding-medicines"],
     "trigger": "a baby, breastfeeding or formula, and vitamin D",
     "line": "Babies from birth to 1 year should have a daily vitamin D supplement, unless they drink "
             "500ml or more of infant formula a day.",
     "source": ("NHS: Vitamin D", "https://www.nhs.uk/conditions/vitamins-and-minerals/vitamin-d/"),
     "quote": "babies from birth to 1 year of age should have a daily supplement"},

    # ---- children ----------------------------------------------------------------
    {"id": "child-iron-overdose", "topic": "Iron tablets and children", "rank": 1,
     "when": [IRON],
     "trigger": "iron tablets, supplements or drops, or taking iron",
     "line": "Keep iron tablets out of sight and reach of children, as an overdose may be fatal.",
     "source": ("NHS: How and when to take ferrous sulfate",
                "https://www.nhs.uk/medicines/ferrous-sulfate/how-and-when-to-take-ferrous-sulfate/"),
     "quote": "Keep ferrous sulfate out of sight and reach of children, as an overdose may be fatal."},
    {"id": "child-aspirin", "topic": "Aspirin and children", "rank": 1,
     "when": [ASPIRIN, CHILD + "|" + _w(r"reye'?s", r"under 16")], "strip": [ADULT_ASPIRIN],
     "trigger": "aspirin with a child, teenager or baby (\"baby aspirin\" and \"low dose aspirin\" do not count)",
     "line": "Do not give aspirin to children aged 15 or under: it can cause an uncommon but serious "
             "condition called Reye's syndrome.",
     "source": ("NHS: Aspirin for pain relief", "https://www.nhs.uk/medicines/aspirin-for-pain-relief/"),
     "quote": "Do not give aspirin to children aged 15 years or under because it can cause a serious "
              "condition called Reye's syndrome"},
    {"id": "child-cough-cold-medicine", "topic": "Cough and cold medicines for young children", "rank": 1,
     "when": [CHILD, COUGH_MEDICINE],
     "trigger": "a child or baby, and a cough or cold medicine or syrup",
     "line": "Over the counter cough and cold medicines are not recommended for children under 2; "
             "for an older child, check the age on the label first.",
     "source": ("FDA: Should you give kids medicine for coughs and colds?",
                "https://www.fda.gov/consumers/consumer-updates/should-you-give-kids-medicine-coughs-and-colds"),
     "quote": "The FDA doesn't recommend OTC medicines for cough and cold symptoms in children younger than 2."},
    {"id": "child-decongestant", "topic": "Decongestants for children", "rank": 2,
     "when": [CHILD, DECONGESTANT], "over": ["child-cough-cold-medicine"],
     "trigger": "a child or baby, and a decongestant or nasal spray",
     "line": "Not all decongestants are suitable for children: check the leaflet before giving one to "
             "a child, and ask a pharmacist if you are not sure.",
     "source": ("NHS: Decongestants", "https://www.nhs.uk/conditions/decongestants/"),
     "quote": "Check the leaflet that comes with the medicine before giving a decongestant to a child"},
    {"id": "child-ibuprofen-chickenpox", "topic": "Ibuprofen and chickenpox", "rank": 1,
     "when": [CHICKENPOX, NSAID],
     "trigger": "chickenpox, and ibuprofen, anti-inflammatories or painkillers",
     "line": "Do not use ibuprofen for chickenpox unless a doctor tells you to, as it may cause serious "
             "skin infections.",
     "source": ("NHS: Chickenpox", "https://www.nhs.uk/conditions/chickenpox/"),
     "quote": "do not use ibuprofen unless you're told to by a doctor, as it may cause serious skin infections"},
    {"id": "child-teething-gel", "topic": "Teething gels", "rank": 2,
     "when": [_w(r"teething")],
     "trigger": "teething",
     "line": "For a teething baby, use only a gel made for young children: general oral pain relief "
             "gels are not suitable for children.",
     "source": ("NHS: Tips for helping your teething baby",
                "https://www.nhs.uk/baby/babys-development/teething/tips-for-helping-your-teething-baby/"),
     "quote": "General oral pain relief gels are not suitable for children."},
    {"id": "child-salt-gargle", "topic": "Gargling salt water", "rank": 2,
     "when": [_w(r"gargl\w*"), SALT_WATER + "|" + _w(r"salt")],
     "trigger": "gargling, with salt or salt water",
     "line": "Gargling with warm salty water is not for children: they should not try it.",
     "source": ("NHS: Sore throat", "https://www.nhs.uk/conditions/sore-throat/"),
     "quote": "gargle with warm, salty water (children should not try this)"},
    {"id": "child-ear-infection", "topic": "Ear infections in children", "rank": 2,
     "when": [CHILD, EAR_INFECTION],
     "trigger": "a child or baby, and an ear infection or earache",
     "line": "A baby under 12 months who may have an ear infection should see a GP; for an older child, "
             "a pharmacist can help first.",
     "source": ("NHS: Ear infections", "https://www.nhs.uk/conditions/ear-infections/"),
     "quote": "your child is under 12 months old and you think they have an ear infection"},

    # ---- pregnancy -----------------------------------------------------------------
    {"id": "pregnancy-stop-medicine", "topic": "Stopping a prescribed medicine in pregnancy", "rank": 1,
     "when": [PREGNANT, MEDICINE, STOPPING], "unless": [NOT_PREGNANT], "hazard": "stop-medicine",
     "over": ["pregnancy-medicines", "stop-in-place-of-treatment"],
     "trigger": "pregnancy, a medicine, and stopping, skipping or doing without it",
     "line": "In pregnancy, never stop a medicine prescribed to keep you healthy without talking to "
             "your doctor first: stopping could harm you and your baby.",
     "source": ("NHS: Medicines in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/medicines/"),
     "quote": "Stopping taking your medicine could be harmful to both you and your baby."},
    {"id": "pregnancy-epilepsy", "topic": "Epilepsy medicine in pregnancy", "rank": 1,
     "when": [PREGNANT, EPILEPSY], "unless": [NOT_PREGNANT],
     "over": ["epilepsy-medicine", "pregnancy-medicines", "pregnancy-stop-medicine"],
     "trigger": "pregnancy and epilepsy or an epilepsy medicine",
     "line": "If you take epilepsy medicine and are pregnant, keep taking it, and speak to your "
             "specialist team straight away.",
     "source": ("NHS: Epilepsy treatment", "https://www.nhs.uk/conditions/epilepsy/treatment/"),
     "quote": "keep taking your medicine but speak to your specialist team straight away"},
    {"id": "pregnancy-alcohol", "topic": "Alcohol in pregnancy", "rank": 1,
     "when": [PREGNANT, ALCOHOL], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy or trying for a baby, and alcohol, wine, beer or drinking",
     "line": "If you are pregnant or planning a pregnancy, the advice is not to drink alcohol.",
     "source": ("NHS: Drinking alcohol while pregnant",
                "https://www.nhs.uk/pregnancy/keeping-well/drinking-alcohol-while-pregnant/"),
     "quote": "It's recommended that if you're pregnant or planning to become pregnant you should not drink alcohol."},
    {"id": "pregnancy-vitamin-a", "topic": "Vitamin A supplements in pregnancy", "rank": 1,
     "when": [PREGNANT, VITAMIN_A_PREG], "unless": [NOT_PREGNANT],
     "over": ["vitamin-a-limit", "pregnancy-medicines"],
     "trigger": "pregnancy, and vitamin A, retinol, cod or fish liver oil, or multivitamins",
     "line": "In pregnancy, do not take cod liver oil or supplements containing vitamin A, sometimes "
             "called retinol.",
     "source": ("NHS: Pregnancy vitamins and supplements",
                "https://www.nhs.uk/pregnancy/keeping-well/pregnancy-vitamins-and-supplements/"),
     "quote": "Do not take cod liver oil or supplements containing vitamin A (sometimes called retinol) "
              "when you're pregnant."},
    {"id": "pregnancy-liver", "topic": "Liver in pregnancy", "rank": 1,
     "when": [PREGNANT, LIVER], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and liver or liver products (not liver disease or liver tests)",
     "line": "In pregnancy, avoid liver and liver products: they are high in vitamin A, which can be "
             "harmful to your baby.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "Liver and foods containing liver have high levels of vitamin A, which can be harmful to your baby."},
    {"id": "pregnancy-caffeine", "topic": "Caffeine in pregnancy", "rank": 2,
     "when": [PREGNANT, CAFFEINE], "unless": [NOT_PREGNANT, HERBAL_TEA],
     "trigger": "pregnancy, and caffeine, coffee, tea, cola or energy drinks (not herbal tea)",
     "line": "In pregnancy, have no more than 200mg of caffeine a day: it is in coffee, tea, cola, "
             "energy drinks and chocolate.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "you should not have more than 200mg of caffeine a day"},
    {"id": "pregnancy-fish", "topic": "Fish in pregnancy", "rank": 2,
     "when": [PREGNANT, FISH], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and fish, seafood, shellfish, tuna, sushi or mercury (not fish oil)",
     "line": "In pregnancy, avoid shark, swordfish, marlin and raw shellfish, and eat no more than 2 "
             "portions of oily fish and 4 cans of tuna a week.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "tuna – you should not eat more than 4 cans or 2 tuna steaks a week"},
    {"id": "pregnancy-soft-cheese", "topic": "Unpasteurised dairy in pregnancy", "rank": 2,
     "when": [PREGNANT, SOFT_CHEESE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and cheese, unpasteurised or raw milk, or listeria",
     "line": "In pregnancy, avoid unpasteurised milk and some soft cheeses, which may contain listeria "
             "bacteria.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "Unpasteurised or soft ripened dairy foods may contain listeria bacteria, which can cause listeriosis."},
    {"id": "pregnancy-raw-meat", "topic": "Raw and cured meat in pregnancy", "rank": 2,
     "when": [PREGNANT, MEAT], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and meat, steak, cured or deli meats, or toxoplasmosis",
     "line": "In pregnancy, avoid raw, undercooked or cured meat: it carries a small risk of "
             "toxoplasmosis, which can cause miscarriage.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "There's a small risk of getting toxoplasmosis if you eat raw, undercooked or cured meat, "
              "which can cause miscarriage."},
    {"id": "pregnancy-herbal-tea", "topic": "Herbal tea in pregnancy", "rank": 2,
     "when": [PREGNANT, HERBAL_TEA], "unless": [NOT_PREGNANT], "over": ["pregnancy-medicines"],
     "trigger": "pregnancy, and herbal, fruit or liquorice tea",
     "line": "In pregnancy, drink no more than 4 cups of herbal tea a day, and avoid teas containing "
             "liquorice root.",
     "source": ("NHS: Foods to avoid in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/foods-to-avoid/"),
     "quote": "avoid drinking more than 4 cups of herbal tea a day, and avoid teas containing liquorice root"},
    {"id": "pregnancy-exercise", "topic": "Exercise in pregnancy", "rank": 2,
     "when": [PREGNANT, EXERCISE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and exercise, sport, the gym, swimming, diving or being active",
     "line": "In pregnancy, do not scuba dive, and avoid sports where there is a risk of falling or "
             "bumping your tummy.",
     "source": ("NHS: Exercise in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/exercise/"),
     "quote": "do not do sports or activities where there's a risk of falling or bumping your tummy, "
              "like hockey, rugby or mountain biking"},
    {"id": "pregnancy-folic-acid", "topic": "Folic acid before and in pregnancy", "rank": 3,
     "when": [PREGNANT + "|" + _w(r"neural tube", r"spina bifida", r"conceiv\w*", r"conception"), FOLIC], "unless": [NOT_PREGNANT], "over": ["pregnancy-medicines"],
     "trigger": "folic acid or folate, with pregnancy, conceiving, neural tube defects or spina bifida",
     "line": "Take 400 micrograms of folic acid a day while trying to get pregnant and for the first "
             "12 weeks of pregnancy.",
     "source": ("NHS: Pregnancy vitamins and supplements",
                "https://www.nhs.uk/pregnancy/keeping-well/pregnancy-vitamins-and-supplements/"),
     "quote": "Take folic acid for the first 12 weeks of pregnancy."},
    {"id": "pregnancy-medicines", "topic": "Medicines, supplements and remedies in pregnancy", "rank": 2,
     "when": [PREGNANT, MEDICINE], "unless": [NOT_PREGNANT],
     "trigger": "pregnancy, and any medicine, supplement, vitamin or herbal remedy",
     "line": "If you are pregnant, check with your doctor, midwife or pharmacist before taking any "
             "medicine, and tell them about any herbal remedies.",
     "source": ("NHS: Medicines in pregnancy", "https://www.nhs.uk/pregnancy/keeping-well/medicines/"),
     "quote": "Tell your midwife, doctor or pharmacist if you're using herbal, homeopathic or aromatherapy "
              "remedies or therapies."},

    # ---- breastfeeding -----------------------------------------------------------
    {"id": "breastfeeding-medicines", "topic": "Medicines while breastfeeding", "rank": 2,
     "when": [BREASTFEEDING, MEDICINE + "|" + ALCOHOL],
     "trigger": "breastfeeding, and any medicine, supplement or herbal remedy",
     "line": "If you are breastfeeding, check with a GP, midwife, health visitor or pharmacist before "
             "taking a medicine or herbal remedy.",
     "source": ("NHS: Medicines and breastfeeding",
                "https://www.nhs.uk/conditions/baby/breastfeeding-and-bottle-feeding/breastfeeding-and-lifestyle/medicines/"),
     "quote": "Always check with a GP, your midwife, health visitor or a pharmacist, who can advise you."},

]

for _e in ENTRIES:
    _e["checked"] = CHECKED
    _e["_when"] = [re.compile(p, _FLAGS) for p in _e["when"]]
    _e["_unless"] = [re.compile(p, _FLAGS) for p in _e.get("unless", ())]
    _e["_strip"] = [re.compile(p, _FLAGS) for p in _e.get("strip", ())]
BY_ID = {e["id"]: e for e in ENTRIES}
LINES = frozenset(e["line"] for e in ENTRIES)


def normalise(claim: str) -> str:
    """The claim as the rules read it: straight quotes, hyphens as spaces
    between words, one space, lower case."""
    text = str(claim or "").replace("’", "'").replace("‘", "'")
    text = re.sub(r"(?<=\w)[-‐‑](?=\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _fires(entry: dict, text: str) -> bool:
    for pattern in entry["_strip"]:
        text = pattern.sub(" ", text)
    return (all(p.search(text) for p in entry["_when"])
            and not any(p.search(text) for p in entry["_unless"]))


def _said(hazard: str, shown: str) -> bool:
    """An existing guard sentence about this hazard is already on the answer."""
    if hazard == "stop-medicine":
        import verdict  # the guard's own sentences, so they cannot drift apart
        return verdict.FORGO_DOCTOR in shown or "Stopping a prescribed medicine" in shown
    return False


def match(claim: str, shown: str = "") -> list[dict]:
    """The entries for this claim, most serious first, at most MAX_LINES.
    `shown` is the text already on the answer: an entry whose hazard an
    existing guard sentence covers there stays silent."""
    text = normalise(claim)
    hits = [e for e in ENTRIES if _fires(e, text)]
    covered = {o for e in hits for o in e.get("over", ())}
    hits = [e for e in hits if e["id"] not in covered
            and not (e.get("hazard") and _said(e["hazard"], shown))]
    hits.sort(key=lambda e: e["rank"])  # stable: library order breaks ties
    return hits[:MAX_LINES]


def lines_for(claim: str, shown: str = "") -> list[str]:
    return [e["line"] for e in match(claim, shown)]


def attach(claim: str, tldr: str, explanation: str) -> str:
    """
    The explanation with this claim's safety lines added once, at its end,
    where the harm guard's sentence goes. Nothing else changes, and a line
    already on the answer (in the short answer or the explanation) is not
    added again.
    """
    explanation = str(explanation or "")
    shown = f"{tldr or ''} {explanation}"
    new = [ln for ln in lines_for(claim, shown) if ln not in shown]
    return " ".join([explanation.strip()] + new).strip() if new else explanation
