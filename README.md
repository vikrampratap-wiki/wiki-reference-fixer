# विकिपीडिया सन्दर्भ सुधार उपकरण (Hindi Wikipedia Reference Fixer)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Wikimedia Toolforge](https://img.shields.io/badge/Wikimedia-Toolforge_Ready-blue.svg)](https://wikitech.wikimedia.org/wiki/Help:Toolforge)
[![E2E Test Suite](https://img.shields.io/badge/E2E_Tests-64%2F64_Passed_(100%25)-success.svg)](#परीक्षण-एवं-गुणवत्ता)
[![Zero Auto-Save](https://img.shields.io/badge/Policy-Zero_Auto--Save-brightgreen.svg)](#ज़ीरो-ऑटो-सेव-एवं-मानवीय-समीक्षा)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

हिन्दी विकिपीडिया (`hi.wikipedia.org`) के लिए निर्मित एक सुरक्षित, मानवीय नियंत्रण-युक्त और पूर्ण-स्टैक वेब एप्लिकेशन (Full-Stack Web Application)। यह उपकरण विकिपीडिया लेखों में मौजूद नग्न URLs (Bare URLs), खराब/अधूरे साँचों और पुराने सन्दर्भों का स्वतः विश्लेषण कर मानक Wikitext एवं Wikimedia Citoid REST API के अनुरूप सटीक सुधार प्रस्तावित करता है।

---

## मुख्य विशेषताएँ (Core Features)

### 1. विकिटेक्स्ट AST पार्सर एवं Citoid इंजन (AST Parser & Citoid Engine)
- **लॉसलेस पार्सिंग:** `mwparserfromhell` पर आधारित पार्सर जो टिप्पणियों (`<!-- ... -->`) और `<nowiki>` टैग्स को सुरक्षित रखते हुए केवल सक्रिय `<ref>` टैग्स का विश्लेषण करता है।
- **Excluded-Zone Progressive Cursor:** डुप्लिकेट टैग्स और कमेंट्स के टकराव से मुक्त 1-टू-1 कैरेक्टर स्पैन रेजोल्यूशन।
- **Citoid REST API एकीकरण:** Wikimedia API (`/api/rest_v1/data/citation/mediawiki/{url}`) से मेटाडेटा लेकर नग्न URLs को मानक `{{Cite web}}`, `{{Cite news}}`, `{{Cite book}}`, या `{{Cite journal}}` में बदलना।
- **Wikimedia नीति अनुपालन:** 1 rps (request per second) दर-सीमा, `maxlag=5` बैकऑफ़, और समर्पित `User-Agent` हेडर।
- **दोहरा फ़ालबैक (Dual Fallback):** Citoid 400/404 या टाइमआउट की स्थिति में सीधे HTML `<title>` व डोमेन नाम से सुरक्षित साँचा निर्माण।
- **सिंटैक्स रिपेयर इंजन:** अधूरे कोष्ठक (`{{` व `}}`), छूटे पाइप (`||` $\to$ `|`), विकिटेबल सुरक्षा, और `deadurl=yes` को आधुनिक `url-status=dead` में बदलना।

### 2. वेबसाइट एडमिन RBAC व सुरक्षा (Website Admin RBAC & Security)
- **वेबसाइट स्वामी / प्रशासक:** केवल **विक्रम प्रताप (`Vikram Pratap`)** वेबसाइट एडमिन के रूप में मान्य।
- **गतिशील विकिपीडिया उप-पृष्ठ द्वारा सह-प्रशासक प्रबंधन:** अधिकृत सह-प्रशासकों की सूची विक्रम प्रताप के निजी विकिपीडिया उप-पृष्ठ (`सदस्य:विक्रम_प्रताप/wiki-ref-fixer-admins.json`) से MediaWiki Action API द्वारा सुरक्षित रूप से पढ़ी जाती है (10 मिनट कैशिंग)।
- **सख्त 404 सुरक्षा (Strict Information Concealment):** गैर-प्रशासकों द्वारा प्रशासकीय रूट्स (`/admin`, `/api/admin/*`) खोलने पर सख्त `404 Not Found` प्राप्त होता है। विकिपीडिया के अन्य सदस्य या प्रबंधक इस साइट पर सामान्य उपयोगकर्ता रहते हैं।
- **आपातकालीन किल-स्विच (Emergency Kill-Switch):** आवश्यकता पड़ने पर एडमिन द्वारा सभी बाहरी API कॉल्स को तत्काल रोक (HTTP 503 with `Retry-After: 300`) लगाने की सुविधा।
- **ऑडिट लॉगिंग:** सभी प्रशासकीय और स्कैन क्रियाकलापों का IP-हैशिंग युक्त ऑडिट लॉग।

### 3. ज़ीरो ऑटो-सेव एवं मानवीय समीक्षा (Zero Auto-Save & Granular Review)
- **ज़ीरो ऑटो-सेव गारंटी:** बिना उपयोगकर्ता की स्पष्ट visual समीक्षा और पुष्टि के विकिपीडिया पर कोई भी बदलाव कभी नहीं भेजा जाता।
- **साइड-बाय-साइड Granular Diff:** प्रत्येक सुधार के लिए स्वतंत्र चेकबॉक्स—उपयोगकर्ता चुन सकता है कि कौन सा सन्दर्भ स्वीकार करना है और कौन सा छोड़ना है।
- **Descending-Span Invariance:** केवल चयनित संशोधनों को उनके अवरोही स्पैन क्रम में लागू किया जाता है, जिससे असंबंधित विकिटेक्स्ट 100% अपरिवर्तित रहता है।
- **स्वचालित संपादन सारांश (Dynamic Hindi Edit Summary):** चयनित सुधारों के आधार पर सटीक हिन्दी सारांश (उदा. `नग्न URL को मानक साँचों में बदला | खराब साँचों की मरम्मत ([[WP:REF|सन्दर्भ सुधार उपकरण]])`)।
- **ऑफ़लाइन ड्राफ़्ट व पैच निर्यात:** सुधारे गए पाठ को कॉपी करने या `.patch` फ़ाइल डाउनलोड करने की सुविधा।

### 4. 8 सहायक मॉड्यूल (Companion Helper Modules)
1. **Duplicate Ref Merger:** समान URL या सामग्री वाले संदर्भों को `<ref name="..."/>` में विलय करने का प्रस्ताव।
2. **Duplicate Name Conflict Resolver:** एक ही नाम वाले भिन्न संदर्भों में डेटा ओवरराइट रोके बिना विरोध अलर्ट।
3. **Dead Link & Archive Helper:** 404 कड़ियों की पहचान और Wayback Machine (Internet Archive) API से आर्काइव जोड़ना।
4. **Reference Error Scanner:** रिक्त ref, अपरिभाषित नाम, और बिना संदर्भ सूची वाले लेखों की पहचान।
5. **Reference Health Scorecard:** सन्दर्भ स्वास्थ्य स्कोर (0–100) व ग्रेड (उत्कृष्ट, मध्यम, सुधार आवश्यक)।
6. **ISBN/ISSN/DOI & Date Validator:** पुस्तक व शोध-पत्र पहचानकर्ताओं का चेकसम सत्यापन व देवनागरी अंकों का मानकीकरण।
7. **Ref Group Consistency Checker:** `<ref group="...">` और ग्रुप संदर्भ सूची टैग का मिलान।
8. **Unreliable Domain Flag:** WP:RSP के आधार पर सोशल मीडिया/ब्लॉग स्रोतों पर सलाहकार चेतावनी बैज (बिना स्रोत हटाए)।

---

## तकनीकी ढाँचा (Tech Stack)

| घटक | तकनीक | विवरण |
|-----|--------|-------|
| बैकएंड | Python 3.11+, FastAPI, Uvicorn | उच्च-प्रदर्शन एसिंक्रोनस REST API |
| पार्सर | `mwparserfromhell` | विकिटेक्स्ट AST विश्लेषण व पुनर्निर्माण |
| डेटाबेस | SQLite (WAL mode) + SQLAlchemy Async | कतार, कैश, और ऑडिट लॉग प्रबंधन |
| HTTP क्लाइंट | HTTPX (Async) | Wikimedia rate limit (1 rps) व maxlag=5 बैकऑफ़ |
| प्रमाणीकरण | Wikimedia OAuth 2.0 + ItsDangerous | सुरक्षित हस्ताक्षरित सत्र कुकीज़ |
| फ्रंटएंड | Vanilla JS (ES6+), HTML5, CSS3 | हल्का, तेज़, मोबाइल-फ़र्स्ट, 100% हिन्दी इंटरफ़ेस |
| परिनियोजन | Wikimedia Toolforge / Docker | टूलफोर्ज कुबेरनेट्स व स्टैंडर्ड लिनक्स |

---

## डायरेक्टरी संरचना (Project Layout)

```
wiki-reference-fixer/
├── app/
│   ├── main.py                     # FastAPI ऐप्लिकेशन व लाइफ़स्पैन
│   ├── core/
│   │   ├── config.py               # Pydantic सेटिंग्स व पर्यावरण चर
│   │   └── security.py             # सत्र कुकीज़ व सुरक्षा
│   ├── db/
│   │   ├── database.py             # SQLite WAL मोड इंजन व सेशन
│   │   └── models.py               # डेटाबेस तालिकाओं के मॉडल्स
│   ├── middleware/
│   │   └── rbac.py                 # एडमिन 404 व किल-स्विच मिडलवेयर
│   ├── services/
│   │   ├── http_client.py          # HTTPX एसिंक्रोनस क्लाइंट
│   │   ├── auth.py                 # OAuth व विकिपीडिया उप-पृष्ठ सत्यापन
│   │   ├── citoid.py               # Citoid REST API व HTML फ़ालबैक
│   │   ├── parser.py               # AST पार्सर व Descending Reconstructor
│   │   ├── syntax_repair.py        # सिंटैक्स मरम्मत व CS1 आधुनिकीकरण
│   │   ├── audit.py                # ऑडिट लॉगर
│   │   └── helpers/                # 8 सहायक मॉड्यूल
│   │       ├── duplicate_merger.py # डुप्लिकेट सन्दर्भ विलय
│   │       ├── conflict_resolver.py# नाम विरोध निवारक
│   │       ├── dead_links.py       # मृत कड़ी व आर्काइव
│   │       ├── error_scanner.py    # सन्दर्भ त्रुटि स्कैनर
│   │       ├── scorecard.py        # स्वास्थ्य स्कोरकार्ड (0-100)
│   │       ├── validator.py        # ISBN/DOI/देवनागरी सत्यापन
│   │       ├── group_checker.py    # ग्रुप संगति जाँचक
│   │       ├── domain_advisory.py  # WP:RSP अविश्वसनीय स्रोत बैज
│   │       └── local_draft.py      # पैच व ड्राफ़्ट निर्यात
│   └── api/
│       ├── auth.py                 # लॉगिन, कॉलबैक व टोकन रूट्स
│       ├── admin.py                # गोपनीय एडमिन पैनल व API
│       ├── scan.py                 # लेख व सन्दर्भ विश्लेषण API
│       ├── diff.py                 # Granular Diff व Zero Auto-Save API
│       └── helpers.py              # 8 सहायक मॉड्यूल के API रूट्स
├── templates/
│   └── index.html                  # 100% हिन्दी मोबाइल-फ़र्स्ट यूआई
├── e2e_tests/                      # स्वतंत्र Opaque-Box E2E टेस्ट सूट (64 टेस्ट)
├── tests/                          # Pytest यूनिट व इंटीग्रेशन टेस्ट्स (300+ टेस्ट)
├── Procfile                        # Wikimedia Toolforge वेबसर्विस निर्देश
├── webservice.json                 # Toolforge कुबेरनेट्स कॉन्फ़िगरेशन
├── requirements.txt                # पायथन निर्भरताएँ
└── .env.example                    # पर्यावरण चर उदाहरण
```

---

## स्थानीय स्थापना एवं संचालन (Local Development Setup)

### 1. रिपॉजिटरी क्लोन करें:
```bash
git clone https://github.com/vikrampratap-wiki/wiki-reference-fixer.git
cd wiki-reference-fixer
```

### 2. वर्चुअल वातावरण बनाएँ व निर्भरताएँ स्थापित करें:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. पर्यावरण चर कॉन्फ़िगर करें:
```bash
cp .env.example .env
# .env फ़ाइल में अपनी आवश्यकतानुसार कुंजियाँ व सेटिंग्स भरें
```

### 4. सर्वर प्रारंभ करें:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
ब्राउज़र में `http://localhost:8000` खोलें।

---

## विकिमीडिया टूलफोर्ज परिनियोजन (Wikimedia Toolforge Deployment)

यह प्रोजेक्ट विकिमीडिया टूलफोर्ज (Wikimedia Toolforge) परिनियोजन के लिए पूर्णतः तैयार है।

### 1. टूलफोर्ज बैस्टियन में लॉगिन करें:
```bash
ssh <username>@login.toolforge.org
become <toolname>
```

### 2. कोड क्लोन करें व वातावरण तैयार करें:
```bash
git clone https://github.com/vikrampratap-wiki/wiki-reference-fixer.git src
cd src
python3 -m venv ~/www/python/venv
source ~/www/python/venv/bin/activate
pip install -r requirements.txt
```

### 3. कुबेरनेट्स वेबसर्विस प्रारंभ करें:
`Procfile` और `webservice.json` प्रोजेक्ट रूट में पहले से उपलब्ध हैं:
```bash
webservice --backend=kubernetes python3.11 start
```

### 4. वेबसर्विस स्थिति जाँचें:
```bash
webservice status
```

---

## डॉकर द्वारा संचालन (Docker Deployment)

```bash
docker build -t wiki-reference-fixer .
docker run -d -p 8000:8000 --env-file .env wiki-reference-fixer
```

---

## परीक्षण एवं गुणवत्ता (Testing & Quality Assurance)

संपूर्ण कोडबेस पर स्वचालित बहु-स्तरीय परीक्षण लागू हैं:

### 1. पूर्ण Pytest टेस्ट सूट चलाएँ:
```bash
.venv/bin/pytest tests/ -v
```

### 2. स्वतंत्र Opaque-Box E2E टेस्ट सूट (64 टेस्ट्स, 4 टीयर्स) चलाएँ:
```bash
.venv/bin/python e2e_tests/runner.py -v
```

### 3. लिंटिंग एवं टाइप चेकिंग:
```bash
.venv/bin/flake8 app/ tests/
.venv/bin/mypy app/ tests/
```

---

## योगदान एवं लाइसेंस (License & Attribution)

- **निर्माता / व्यवस्थापक:** विक्रम प्रताप ([@vikrampratap-wiki](https://github.com/vikrampratap-wiki))
- **लाइसेंस:** [MIT License](LICENSE)
- **समुदाय दिशानिर्देश:** विकिपीडिया बॉट नीति एवं [विकिपीडिया:विश्वसनीय स्रोत (WP:RS)](https://hi.wikipedia.org/wiki/विकिपीडिया:विश्वसनीय_स्रोत) के पूर्णतः अनुरूप।
