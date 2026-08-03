from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GRAPH_FILE_NAME = (
    "wicara_kurikulum_merdeka_graph_complete_with_matematika_tingkat_lanjut.json"
)

SUBJECT_ALIASES = {
    "math": "matematika",
    "mathematics": "matematika",
    "advanced math": "matematika_tingkat_lanjut",
    "advanced mathematics": "matematika_tingkat_lanjut",
    "matematika lanjut": "matematika_tingkat_lanjut",
    "physics": "fisika",
    "chemistry": "kimia",
    "biology": "biologi",
    "science": "ipa",
}

SUBJECT_DISPLAY_ORDER = {
    "matematika": 1,
    "ipas": 2,
    "ipa": 3,
    "fisika": 4,
    "kimia": 5,
    "biologi": 6,
    "matematika_tingkat_lanjut": 7,
}

SUBJECT_LABEL_EN = {
    "matematika": "Mathematics",
    "ipas": "Science and Social Studies",
    "ipa": "Integrated Science",
    "fisika": "Physics",
    "kimia": "Chemistry",
    "biologi": "Biology",
    "matematika_tingkat_lanjut": "Advanced Mathematics",
}

DOMAIN_LABEL_EN = {
    "Aljabar": "Algebra",
    "Aljabar dan Fungsi": "Algebra and Functions",
    "Analisis Data dan Peluang": "Data Analysis and Probability",
    "Benda dan Sifatnya": "Objects and Their Properties",
    "Bilangan": "Numbers",
    "Energi dan Perubahannya": "Energy and Its Changes",
    "Geometri": "Geometry",
    "Pengukuran": "Measurement",
    "Data dan Peluang": "Data and Probability",
    "Statistika": "Statistics",
    "Peluang": "Probability",
    "Fungsi": "Functions",
    "Kalkulus": "Calculus",
    "Keterampilan Proses": "Process Skills",
    "Lingkungan": "Environment",
    "Mekanika": "Mechanics",
    "Gelombang": "Waves",
    "Listrik dan Magnet": "Electricity and Magnetism",
    "Struktur Materi": "Structure of Matter",
    "Makhluk Hidup": "Living Things",
    "Bumi dan Antariksa": "Earth and Space",
    "Pemahaman Biologi": "Biology Concepts",
    "Pemahaman Fisika": "Physics Concepts",
    "Pemahaman Kimia": "Chemistry Concepts",
    "Zat dan Sifatnya": "Matter and Its Properties",
    "General": "General",
}

LABEL_PHRASE_EN = {
    "anggota tubuh": "body parts",
    "alat ukur dan ketelitian": "measurement tools and precision",
    "analisis data dan peluang": "data analysis and probability",
    "angka penting dan ketidakpastian": "significant figures and uncertainty",
    "aplikasi spldv": "applications of two-variable linear systems",
    "akar real dan imajiner": "real and imaginary roots",
    "asam basa": "acids and bases",
    "asosiasi variabel kategorikal": "association of categorical variables",
    "asosiasi variabel numerikal": "association of numerical variables",
    "arus listrik dan rangkaian dc": "electric current and DC circuits",
    "bangun datar": "plane shapes",
    "bangun ruang": "solid shapes",
    "barisan aritmetika": "arithmetic sequences",
    "barisan geometri": "geometric sequences",
    "benda dan sifat sederhana": "simple objects and properties",
    "benda dan zat": "objects and matter",
    "bentuk permukaan bumi dan sumber daya alam": "Earth surface forms and natural resources",
    "besaran dan satuan": "quantities and units",
    "bilangan berpangkat": "exponents",
    "bilangan bulat": "integers",
    "bilangan cacah": "whole numbers",
    "bilangan desimal": "decimal numbers",
    "bilangan irasional": "irrational numbers",
    "bilangan rasional": "rational numbers",
    "bilangan real": "real numbers",
    "bunga majemuk": "compound interest",
    "bunga tunggal": "simple interest",
    "bagian tubuh manusia dan pancaindra": "human body parts and senses",
    "bagian tumbuhan dan fungsinya": "plant parts and their functions",
    "bioteknologi modern pengantar": "introduction to modern biotechnology",
    "bumi bulan matahari dan tata surya": "Earth, Moon, Sun, and the solar system",
    "cahaya bunyi dan indra": "light, sound, and senses",
    "campuran dan pemisahan sederhana": "simple mixtures and separation",
    "ciri makhluk hidup": "characteristics of living things",
    "cuaca dan musim sehari hari": "weather and seasons in daily life",
    "daur air dan cuaca": "water cycle and weather",
    "data dan peluang": "data and probability",
    "daerah penyelesaian": "solution regions",
    "deret aritmetika": "arithmetic series",
    "deret geometri": "geometric series",
    "diagram batang": "bar charts",
    "diagram lingkaran": "pie charts",
    "domain kodomain range": "domain, codomain, and range",
    "ekspresi aljabar": "algebraic expressions",
    "faktorisasi prima": "prime factorization",
    "fungsi dasar": "basic functions",
    "fungsi kuadrat": "quadratic functions",
    "fungsi linear": "linear functions",
    "fungsi nonlinear": "nonlinear functions",
    "faktor yang memengaruhi laju reaksi": "factors affecting reaction rates",
    "gerak benda dorongan dan tarikan": "object motion, pushes, and pulls",
    "gerak bumi bulan matahari": "Earth, Moon, and Sun motion",
    "etika teknologi biologi": "ethics of biotechnology",
    "entalpi reaksi": "reaction enthalpy",
    "evaluasi laporan statistika media": "evaluating media statistics reports",
    "fisika inti": "nuclear physics",
    "frekuensi harapan": "expected frequency",
    "frekuensi harapan kejadian majemuk": "expected frequency of compound events",
    "garis bilangan": "number lines",
    "garis bilangan dan perbandingan bilangan": "number lines and number comparison",
    "grafik fungsi": "function graphs",
    "gaya antar molekul": "intermolecular forces",
    "hakikat ilmu": "nature of science",
    "hakikat ilmu kimia": "nature of chemistry",
    "hakikat sains dan kerja ilmiah": "nature of science and scientific work",
    "hewan dan tumbuhan di lingkungan sekitar": "animals and plants in the surrounding environment",
    "hukum kekekalan massa": "law of conservation of mass",
    "hukum dasar kimia dalam perhitungan sederhana": "basic chemistry laws in simple calculations",
    "hukum perbandingan tetap": "law of definite proportions",
    "hubungan antar sudut": "angle relationships",
    "inovasi teknologi biologi lanjut": "advanced biotechnology innovation",
    "interaksi makhluk hidup dan lingkungan": "interactions between living things and the environment",
    "interaksi biotik abiotik": "biotic and abiotic interactions",
    "jaring-jaring": "nets",
    "kalimat matematika": "mathematical sentences",
    "keterampilan investigasi variabel sederhana": "simple variable investigation skills",
    "keterampilan observasi dan bertanya": "observation and questioning skills",
    "kelipatan dan keterbagian": "multiples and divisibility",
    "keselamatan kerja": "lab safety",
    "keselamatan kerja laboratorium kimia": "chemistry laboratory safety",
    "kesetimbangan kimia": "chemical equilibrium",
    "kimia dalam kehidupan sehari hari": "chemistry in daily life",
    "kimia lingkungan dan pemanasan global": "environmental chemistry and global warming",
    "laju perubahan": "rate of change",
    "laju reaksi": "reaction rates",
    "literasi finansial": "financial literacy",
    "lingkungan bersih sampah dan kebiasaan menjaga alam": "clean environments, waste, and nature-care habits",
    "litosfer hidrosfer atmosfer": "lithosphere, hydrosphere, and atmosphere",
    "listrik statis lanjut": "advanced static electricity",
    "luas lingkaran": "circle area",
    "luas permukaan": "surface area",
    "makhluk hidup": "living things",
    "membandingkan dua kelompok data": "comparing two data groups",
    "menganalisis data ipa": "analyzing science data",
    "mengomunikasikan hasil penyelidikan": "communicating investigation results",
    "mengumpulkan data pengamatan": "collecting observation data",
    "mengenal uang dan nilai": "recognizing money and value",
    "merancang penyelidikan sederhana": "designing simple investigations",
    "metode ilmiah": "scientific method",
    "model bunga majemuk lanjut": "advanced compound interest models",
    "nanoteknologi pengantar": "introduction to nanotechnology",
    "nilai tempat": "place value",
    "nomor atom nomor massa isotop": "atomic number, mass number, and isotopes",
    "operasi aritmetika": "arithmetic operations",
    "operasi bilangan": "number operations",
    "operasi hitung campuran": "mixed operations",
    "operasi pecahan": "fraction operations",
    "partikel atom dan molekul dasar": "basic atomic and molecular particles",
    "pangkat pecahan": "fractional exponents",
    "pancaindra": "senses",
    "pecahan desimal dan persen": "fractions, decimals, and percents",
    "pecahan senilai": "equivalent fractions",
    "pecahan satuan": "unit fractions",
    "peluang dasar": "basic probability",
    "peluang sederhana": "simple probability",
    "peluang sehari hari informal": "informal everyday probability",
    "pengumpulan dan penyajian data": "data collection and presentation",
    "pengelompokan hewan dan tumbuhan": "classifying animals and plants",
    "pengukuran": "measurement",
    "pengukuran dalam ipa": "measurement in science",
    "penjumlahan dan pengurangan": "addition and subtraction",
    "penyelidikan statistika bivariat": "bivariate statistics investigations",
    "pencemaran lingkungan": "environmental pollution",
    "pencemaran lingkungan dari sudut pandang fisika": "environmental pollution from a physics perspective",
    "perubahan benda sederhana": "simple changes in objects",
    "perubahan fisika dan kimia": "physical and chemical changes",
    "perubahan fisika dan kimia dasar": "basic physical and chemical changes",
    "perubahan iklim dan mitigasi": "climate change and mitigation",
    "perubahan iklim dan pemanasan global": "climate change and global warming",
    "perubahan lingkungan dan solusi lokal": "environmental change and local solutions",
    "perubahan materi": "matter changes",
    "perubahan materi dan reaksi kimia": "matter changes and chemical reactions",
    "perubahan proporsional ukuran bangun": "proportional changes in shape size",
    "persamaan eksponensial": "exponential equations",
    "persamaan kuadrat": "quadratic equations",
    "persamaan linear": "linear equations",
    "persamaan reaksi kimia sederhana": "simple chemical equations",
    "persamaan sederhana": "simple equations",
    "pertidaksamaan linear": "linear inequalities",
    "pola bilangan": "number patterns",
    "polimer dan pemanfaatannya": "polymers and their uses",
    "pubertas dan kesehatan tubuh dasar": "puberty and basic body health",
    "pertumbuhan dan perkembangan tumbuhan": "plant growth and development",
    "inovasi teknologi biologi": "biotechnology innovation",
    "laporan penyelidikan fisika": "physics investigation reports",
    "orde reaksi pengantar": "introduction to reaction order",
    "representasi fungsi": "function representations",
    "reaksi redoks": "redox reactions",
    "sifat bilangan": "number properties",
    "sifat fisika dan kimia": "physical and chemical properties",
    "sifat fisika dan kimia zat": "physical and chemical properties of matter",
    "siklus hidup hewan dan tumbuhan": "life cycles of animals and plants",
    "sistem pencernaan lanjut": "advanced digestive system",
    "sistem peredaran darah lanjut": "advanced circulatory system",
    "sistem periodik unsur pengantar": "introduction to the periodic table of elements",
    "sistem pernapasan lanjut": "advanced respiratory system",
    "sistem reproduksi lanjut": "advanced reproductive system",
    "sistem persamaan linear": "systems of linear equations",
    "sistem pertidaksamaan linear": "systems of linear inequalities",
    "sumber daya alam dan perubahan lingkungan": "natural resources and environmental change",
    "stoikiometri reaksi": "reaction stoichiometry",
    "struktur atom": "atomic structure",
    "struktur bumi": "Earth structure",
    "suhu kalor lanjut": "advanced temperature and heat",
    "teorema pythagoras": "Pythagorean theorem",
    "usaha energi dan daya lanjut": "advanced work, energy, and power",
    "variabel dalam penyelidikan fisika": "variables in physics investigations",
    "volume prisma": "prism volume",
    "wujud zat dan perubahan wujud": "states of matter and changes of state",
    "energi dan daya": "energy and power",
    "ikatan ion": "ionic bonds",
    "ikatan kovalen": "covalent bonds",
    "ikatan logam": "metallic bonds",
    "kalor dan perpindahan panas": "heat and heat transfer",
    "literasi finansial sd": "elementary financial literacy",
    "luas permukaan prisma tabung bola limas kerucut": "surface area of prisms, cylinders, spheres, pyramids, and cones",
    "panjang busur lingkaran": "circle arc length",
    "peluang bersyarat": "conditional probability",
    "peluang kejadian majemuk": "probability of compound events",
    "perbandingan trigonometri segitiga siku siku": "right-triangle trigonometric ratios",
    "perpindahan kalor": "heat transfer",
    "persamaan linear satu variabel": "one-variable linear equations",
    "pertidaksamaan linear satu variabel": "one-variable linear inequalities",
    "rangkaian listrik sederhana": "simple electric circuits",
    "sel volta": "voltaic cells",
    "tata surya": "solar system",
    "volume prisma tabung bola limas kerucut": "volume of prisms, cylinders, spheres, pyramids, and cones",
}

LABEL_WORD_EN = {
    "aljabar": "algebra",
    "akibat": "caused by",
    "akar": "roots",
    "alam": "natural",
    "alat": "tools",
    "aliran": "flow",
    "alternatif": "alternative",
    "antar": "inter",
    "anuitas": "annuities",
    "aplikasi": "applications",
    "aritmetika": "arithmetic",
    "arus": "current",
    "asam": "acids",
    "asosiasi": "association",
    "asosiatif": "associative",
    "atmosfer": "atmosphere",
    "atom": "atomic",
    "awal": "introductory",
    "bagian": "parts",
    "bahan": "materials",
    "baku": "standard",
    "barisan": "sequences",
    "batang": "bar",
    "basa": "bases",
    "bebas": "independent",
    "benda": "objects",
    "berat": "weight",
    "bersih": "clean",
    "bersyarat": "conditional",
    "berulang": "repeating",
    "besar": "large",
    "besaran": "quantities",
    "bentuk": "forms",
    "berpangkat": "exponents",
    "bertanya": "questioning",
    "biologi": "biology",
    "bioteknologi": "biotechnology",
    "biodiversitas": "biodiversity",
    "biotik": "biotic",
    "bivariat": "bivariate",
    "bola": "sphere",
    "bulat": "integers",
    "bulan": "moon",
    "bumi": "earth",
    "bunga": "interest",
    "busur": "arcs",
    "cahaya": "light",
    "campuran": "mixtures",
    "cacah": "whole",
    "chatelier": "Chatelier",
    "ciri": "characteristics",
    "cuaca": "weather",
    "daerah": "regions",
    "darah": "blood",
    "data": "data",
    "dasar": "basic",
    "datar": "plane",
    "daur": "cycle",
    "dc": "DC",
    "dengan": "with",
    "desimal": "decimals",
    "deret": "series",
    "di": "in",
    "difusi": "diffusion",
    "dilatasi": "dilations",
    "dalam": "in",
    "dan": "and",
    "dari": "from",
    "digital": "digital",
    "diketahui": "unknown",
    "dimensi": "dimensional",
    "dinamika": "dynamics",
    "dinamis": "dynamic",
    "distributif": "distributive",
    "dna": "DNA",
    "domain": "domain",
    "dorongan": "pushes",
    "dua": "two",
    "efisiensi": "efficiency",
    "ekosistem": "ecosystems",
    "ekskresi": "excretory",
    "ekuivalen": "equivalent",
    "elektrolisis": "electrolysis",
    "elektron": "electron",
    "elektromagnetik": "electromagnetic",
    "eksponensial": "exponential",
    "endokrin": "endocrine",
    "energi": "energy",
    "entalpi": "enthalpy",
    "enzim": "enzymes",
    "estimasi": "estimation",
    "etika": "ethics",
    "eukariot": "eukaryotic",
    "evaluasi": "evaluation",
    "evolusi": "evolution",
    "fase": "phase",
    "faktor": "factors",
    "faktorisasi": "factorization",
    "fisik": "physical",
    "fisika": "physics",
    "fluida": "fluid",
    "fotosintesis": "photosynthesis",
    "fpb": "GCF",
    "frekuensi": "frequency",
    "fungsi": "functions",
    "garam": "salts",
    "garis": "lines",
    "gaya": "forces",
    "gejala": "phenomena",
    "gelombang": "waves",
    "gen": "genes",
    "generalisasi": "generalization",
    "geometri": "geometry",
    "gerak": "motion",
    "gerbang": "gates",
    "gerhana": "eclipses",
    "gesek": "friction",
    "getaran": "vibrations",
    "global": "global",
    "grafik": "graphs",
    "gugus": "functional groups",
    "habitat": "habitats",
    "hakikat": "nature",
    "harapan": "expected",
    "hasil": "results",
    "hayati": "biological",
    "hereditas": "heredity",
    "hess": "Hess",
    "hewan": "animals",
    "hidrokarbon": "hydrocarbons",
    "hidrolisis": "hydrolysis",
    "hidrosfer": "hydrosphere",
    "hidup": "living",
    "histogram": "histograms",
    "hitung": "calculation",
    "homeostasis": "homeostasis",
    "hubungan": "relationships",
    "hukum": "laws",
    "ilmiah": "scientific",
    "ilmu": "science",
    "imajiner": "imaginary",
    "impuls": "impulse",
    "imun": "immune",
    "indra": "senses",
    "induksi": "induction",
    "informal": "informal",
    "inti": "nuclear",
    "interaksi": "interactions",
    "interkuartil": "interquartile",
    "interpretasi": "interpretation",
    "invers": "inverse",
    "investasi": "investment",
    "investigasi": "investigation",
    "ion": "ion",
    "irasional": "irrational",
    "isotop": "isotopes",
    "jajaran": "array",
    "jaring": "net",
    "jaringan": "tissues",
    "jangkauan": "range",
    "jarak": "distance",
    "juring": "sectors",
    "kalimat": "sentences",
    "kalor": "heat",
    "karbon": "carbon",
    "kartesius": "Cartesian",
    "kategorikal": "categorical",
    "kebiasaan": "habits",
    "kebutuhannya": "needs",
    "kejadian": "events",
    "kehidupan": "life",
    "kekekalan": "conservation",
    "keliling": "perimeter",
    "kekongruenan": "congruence",
    "kelipatan": "multiples",
    "kelompok": "groups",
    "kemagnetan": "magnetism",
    "kerja": "work",
    "kerucut": "cones",
    "kesehatan": "health",
    "keselamatan": "safety",
    "kesebangunan": "similarity",
    "keseimbangan": "balance",
    "kesetimbangan": "equilibrium",
    "keterampilan": "skills",
    "ketelitian": "precision",
    "keterbagian": "divisibility",
    "keterkaitan": "relationships",
    "ketidakpastian": "uncertainty",
    "khusus": "special",
    "kimia": "chemistry",
    "kinematika": "kinematics",
    "kirchhoff": "Kirchhoff",
    "klasifikasi": "classification",
    "koordinat": "coordinates",
    "kodomain": "codomain",
    "kombinasi": "combinations",
    "komponen": "components",
    "komposisi": "composition",
    "komutatif": "commutative",
    "konfigurasi": "configuration",
    "konsentrasi": "concentration",
    "konsep": "concepts",
    "konservasi": "conservation",
    "kontrol": "control",
    "konvensional": "conventional",
    "konversi": "conversion",
    "korelasi": "correlation",
    "korosi": "corrosion",
    "kosinus": "cosine",
    "kovalen": "covalent",
    "kpk": "LCM",
    "kromosom": "chromosomes",
    "kuadran": "quadrant",
    "kuantum": "quantum",
    "kuartil": "quartiles",
    "laboratorium": "laboratory",
    "laju": "rate",
    "langit": "sky",
    "lanjut": "advanced",
    "laporan": "reports",
    "larutan": "solutions",
    "le": "Le",
    "lepas": "disjoint",
    "limas": "pyramids",
    "lingkaran": "circles",
    "lingkungan": "environment",
    "linear": "linear",
    "listrik": "electricity",
    "litosfer": "lithosphere",
    "logam": "metallic",
    "logika": "logic",
    "lokal": "local",
    "lurus": "straight",
    "luas": "area",
    "majemuk": "compound",
    "makanan": "food",
    "makhluk": "living things",
    "malam": "night",
    "manusia": "human",
    "masalah": "problems",
    "massa": "mass",
    "matematika": "mathematics",
    "matahari": "sun",
    "materi": "matter",
    "matriks": "matrices",
    "media": "media",
    "meiosis": "meiosis",
    "mekanik": "mechanical",
    "melingkar": "circular",
    "membandingkan": "comparing",
    "membesar": "increasing",
    "membran": "membrane",
    "memengaruhi": "affecting",
    "menganalisis": "analyzing",
    "mengecil": "decreasing",
    "mengenal": "recognizing",
    "mengomunikasikan": "communicating",
    "mengumpulkan": "collecting",
    "menjaga": "protecting",
    "merancang": "designing",
    "mesin": "engines",
    "metabolisme": "metabolism",
    "metode": "method",
    "mean": "mean",
    "median": "median",
    "mitigasi": "mitigation",
    "mitosis": "mitosis",
    "modern": "modern",
    "mol": "mole",
    "molar": "molar",
    "molekul": "molecular",
    "modus": "mode",
    "momentum": "momentum",
    "mutasi": "mutation",
    "nanoteknologi": "nanotechnology",
    "newton": "Newtonian",
    "nilai": "value",
    "nomor": "number",
    "nonlinear": "nonlinear",
    "numerik": "numeric",
    "numerikal": "numerical",
    "observasi": "observation",
    "ohm": "Ohm",
    "optik": "optics",
    "optika": "optics",
    "operasi": "operations",
    "orde": "order",
    "organ": "organs",
    "organel": "organelles",
    "osmosis": "osmosis",
    "pada": "in",
    "pandang": "perspective",
    "panas": "heat",
    "pancaindra": "senses",
    "partikel": "particles",
    "pembagian": "division",
    "pelestarian": "conservation",
    "pemanfaatan": "use",
    "pemanfaatannya": "uses",
    "pembatas": "limiting",
    "pembayaran": "payment",
    "pembelahan": "division",
    "pembulatan": "rounding",
    "pemisahan": "separation",
    "pemodelan": "modeling",
    "pemanasan": "warming",
    "pencar": "scatter",
    "pencemaran": "pollution",
    "pencegahan": "prevention",
    "pencernaan": "digestive",
    "penerapannya": "applications",
    "pengamatan": "observation",
    "pengaruh": "effects",
    "pengantar": "introduction",
    "pengelompokan": "classification",
    "penggunaan": "use",
    "penghematan": "saving",
    "pengukuran": "measurement",
    "pengumpulan": "collection",
    "pengurangan": "subtraction",
    "penting": "significant",
    "penjumlahan": "addition",
    "penyangga": "buffers",
    "penyakit": "diseases",
    "penyajian": "presentation",
    "penyelidikan": "investigation",
    "penyelesaian": "solutions",
    "peranan": "roles",
    "perbandingan": "comparison",
    "peredaran": "circulatory",
    "pereaksi": "reactants",
    "perhitungan": "calculations",
    "periode": "period",
    "periodik": "periodic",
    "perkalian": "multiplication",
    "perkembangan": "development",
    "permukaan": "surface",
    "permutasi": "permutations",
    "pernapasan": "respiratory",
    "persamaan": "equations",
    "persen": "percents",
    "pertidaksamaan": "inequalities",
    "pertumbuhan": "growth",
    "perubahan": "changes",
    "pesawat": "simple machines",
    "peta": "maps",
    "pewarisan": "inheritance",
    "ph": "pH",
    "piktogram": "pictograms",
    "pinjaman": "loans",
    "plot": "plots",
    "pola": "patterns",
    "polimer": "polymers",
    "populasi": "population",
    "posisi": "position",
    "prima": "prime",
    "prinsip": "principle",
    "prisma": "prisms",
    "prokariot": "prokaryotic",
    "proporsi": "proportions",
    "protein": "protein",
    "pubertas": "puberty",
    "puluhan": "tens",
    "pythagoras": "Pythagorean",
    "radioaktivitas": "radioactivity",
    "rantai": "chains",
    "rasio": "ratios",
    "rasional": "rational",
    "ratusan": "hundreds",
    "reaksi": "reactions",
    "real": "real",
    "redoks": "redox",
    "refleksi": "reflections",
    "relasi": "relations",
    "relatif": "relative",
    "relativitas": "relativity",
    "replikasi": "replication",
    "representasi": "representations",
    "reproduksi": "reproductive",
    "respirasi": "respiration",
    "resultan": "resultant",
    "revolusi": "revolution",
    "rotasi": "rotations",
    "ruang": "solid",
    "sains": "science",
    "saling": "mutually",
    "sama": "same",
    "sampai": "up to",
    "sampah": "waste",
    "sampel": "samples",
    "saraf": "nervous",
    "satuan": "units",
    "sebab": "cause",
    "sebagai": "as",
    "secara": "by",
    "sehari": "everyday",
    "sekitar": "surrounding",
    "seleksi": "selection",
    "sederhana": "simple",
    "segitiga": "triangles",
    "sel": "cells",
    "senilai": "equivalent",
    "senyawa": "compounds",
    "siang": "day",
    "siku": "right",
    "siklus": "cycles",
    "simetri": "symmetry",
    "sintesis": "synthesis",
    "sinus": "sine",
    "skala": "scale",
    "skalar": "scalars",
    "sifat": "properties",
    "sistem": "systems",
    "solusi": "solutions",
    "spldv": "two-variable linear systems",
    "statis": "static",
    "statistika": "statistics",
    "stoikiometri": "stoichiometry",
    "struktur": "structure",
    "subatom": "subatomic",
    "sudut": "angles",
    "suhu": "temperature",
    "suku": "interest",
    "sumber": "sources",
    "surya": "solar",
    "taksonomi": "taxonomy",
    "tabel": "tables",
    "tangen": "tangent",
    "tarikan": "pulls",
    "tekanan": "pressure",
    "teknologi": "technology",
    "tempat": "place",
    "teorema": "theorem",
    "terbaik": "best",
    "terikat": "dependent",
    "termodinamika": "thermodynamics",
    "termokimia": "thermochemistry",
    "tetap": "definite",
    "tetapan": "constant",
    "tidak": "not",
    "tiga": "three",
    "tingkat": "levels",
    "titik": "points",
    "titrasi": "titration",
    "translasi": "translations",
    "transpor": "transport",
    "tubuh": "body",
    "tumbuhan": "plants",
    "tumbukan": "collision",
    "tunggal": "simple",
    "uang": "money",
    "ukur": "measuring",
    "ukuran": "size",
    "unit": "unit",
    "unsur": "elements",
    "usaha": "work",
    "variabel": "variables",
    "vektor": "vectors",
    "virus": "viruses",
    "volume": "volume",
    "waktu": "time",
    "wujud": "states",
    "zat": "matter",
    "adiktif": "addictive substances",
    "aditif": "additives",
    "adaptasi": "adaptation",
    "basis": "base",
    "bencana": "disasters",
    "bunyi": "sound",
    "daya": "power",
    "diagram": "diagrams",
    "fungsinya": "functions",
    "gangguan": "disorders",
    "indonesia": "Indonesia",
    "ipa": "science",
    "keanekaragaman": "diversity",
    "kuadrat": "quadratic",
    "magnet": "magnets",
    "musim": "seasons",
    "panjang": "length",
    "pecahan": "fractions",
    "peluang": "probability",
    "perpindahan": "transfer",
    "persegi": "squares",
    "pertanyaan": "questions",
    "rangkaian": "circuits",
    "satu": "one",
    "sd": "elementary school",
    "tabung": "cylinders",
    "tata": "system",
    "teori": "theory",
    "transformasi": "transformations",
    "trigonometri": "trigonometry",
    "volta": "voltaic",
}

PHASE_ORDER = {
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
}

GROUP_X_START = 28.0
GROUP_X_GAP = 302.0
NODE_Y_START = 82.0
NODE_Y_GAP = 70.0


@dataclass(frozen=True)
class SubjectSeed:
    code: str
    name: str
    description: str
    display_order: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ConceptSeed:
    subject_code: str
    code: str
    title: str
    description: str | None
    id_desc: str | None
    en_desc: str | None
    grade_band: str | None
    display_order: int
    layout_x: float
    layout_y: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EdgeSeed:
    from_code: str
    to_code: str
    edge_type: str
    weight: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CurriculumSeedData:
    subjects: list[SubjectSeed]
    concepts: list[ConceptSeed]
    edges: list[EdgeSeed]


def canonical_subject_code(value: str) -> str:
    normalized = _slug(value)
    return SUBJECT_ALIASES.get(normalized, normalized)


def load_kurikulum_merdeka_seed_data(
    graph_path: str | Path | None = None,
) -> CurriculumSeedData:
    if graph_path is None:
        try:
            path = find_default_graph_path()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = _fallback_kurikulum_merdeka_payload()
    else:
        path = Path(graph_path)
        payload = json.loads(path.read_text(encoding="utf-8"))

    metadata = payload.get("metadata", {})
    nodes = [node for node in payload.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in payload.get("edges", []) if isinstance(edge, dict)]

    return _build_seed_data(metadata=metadata, nodes=nodes, edges=edges)


def find_default_graph_path() -> Path:
    module_data_path = Path(__file__).resolve().parent / "data" / GRAPH_FILE_NAME
    if module_data_path.exists():
        return module_data_path

    for parent in Path(__file__).resolve().parents:
        candidate = parent / GRAPH_FILE_NAME
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Could not find {GRAPH_FILE_NAME}. Set graph_path when seeding curriculum."
    )


def _build_seed_data(
    *,
    metadata: dict[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> CurriculumSeedData:
    nodes_by_subject: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        subject_code = canonical_subject_code(_string(node, "subject"))
        if not subject_code:
            continue
        nodes_by_subject.setdefault(subject_code, []).append(node)

    subjects = [
        _subject_seed(subject_code, subject_nodes, metadata)
        for subject_code, subject_nodes in sorted(
            nodes_by_subject.items(),
            key=lambda item: (SUBJECT_DISPLAY_ORDER.get(item[0], 999), item[0]),
        )
    ]

    concepts: list[ConceptSeed] = []
    groups_by_subject = {
        subject_code: _subject_groups(subject_nodes)
        for subject_code, subject_nodes in nodes_by_subject.items()
    }
    concept_order = 1
    for subject_code, subject_nodes in sorted(
        nodes_by_subject.items(),
        key=lambda item: (SUBJECT_DISPLAY_ORDER.get(item[0], 999), item[0]),
    ):
        groups = groups_by_subject[subject_code]
        local_counts_by_group: dict[tuple[str, str], int] = {}
        for node in _sorted_nodes(subject_nodes):
            group_key = _group_key(node)
            group = groups[group_key]
            local_index = local_counts_by_group.get(group_key, 0)
            local_counts_by_group[group_key] = local_index + 1

            concepts.append(
                _concept_seed(
                    subject_code=subject_code,
                    node=node,
                    display_order=concept_order,
                    layout_x=group["x"],
                    layout_y=NODE_Y_START + (local_index * NODE_Y_GAP),
                    local_group_order=local_index + 1,
                )
            )
            concept_order += 1

    known_node_ids = {concept.code for concept in concepts}
    edge_seeds = [
        _edge_seed(edge)
        for edge in edges
        if _string(edge, "from_node_id") in known_node_ids
        and _string(edge, "to_node_id") in known_node_ids
    ]

    return CurriculumSeedData(subjects=subjects, concepts=concepts, edges=edge_seeds)


def _fallback_kurikulum_merdeka_payload() -> dict[str, Any]:
    return {
        "metadata": {
            "curriculum": "kurikulum_merdeka",
            "version": "fallback-dev",
            "generated_at": None,
        },
        "nodes": [
            _fallback_node(
                node_id="km_a_matematika_pola",
                subject="matematika",
                subject_label="Matematika",
                phase="A",
                school_level="SD",
                grade_range="1-2",
                domain="Aljabar",
                difficulty_order=1,
                label="Pola Sederhana",
            ),
            _fallback_node(
                node_id="km_d_matematika_bilangan_bulat",
                subject="matematika",
                subject_label="Matematika",
                phase="D",
                school_level="SMP",
                grade_range="7-9",
                domain="Bilangan",
                difficulty_order=1,
                label="Bilangan Bulat",
            ),
            _fallback_node(
                node_id="km_d_matematika_bilangan_rasional",
                subject="matematika",
                subject_label="Matematika",
                phase="D",
                school_level="SMP",
                grade_range="7-9",
                domain="Bilangan",
                difficulty_order=3,
                label="Bilangan Rasional",
            ),
            _fallback_node(
                node_id="km_d_matematika_bilangan_irasional",
                subject="matematika",
                subject_label="Matematika",
                phase="D",
                school_level="SMP",
                grade_range="7-9",
                domain="Bilangan",
                difficulty_order=4,
                label="Bilangan Irasional",
            ),
            _fallback_node(
                node_id="km_b_ipas_makhluk_hidup",
                subject="ipas",
                subject_label="IPAS",
                phase="B",
                school_level="SD",
                grade_range="3-4",
                domain="Makhluk Hidup",
                difficulty_order=1,
                label="Makhluk Hidup dan Lingkungan",
            ),
            _fallback_node(
                node_id="km_d_ipa_pengukuran",
                subject="ipa",
                subject_label="IPA",
                phase="D",
                school_level="SMP",
                grade_range="7-9",
                domain="Sains",
                difficulty_order=1,
                label="Pengukuran dalam Sains",
            ),
            _fallback_node(
                node_id="km_e_fisika_gerak",
                subject="fisika",
                subject_label="Fisika",
                phase="E",
                school_level="SMA",
                grade_range="10",
                domain="Mekanika",
                difficulty_order=1,
                label="Gerak Lurus",
            ),
            _fallback_node(
                node_id="km_e_kimia_atom",
                subject="kimia",
                subject_label="Kimia",
                phase="E",
                school_level="SMA",
                grade_range="10",
                domain="Struktur Materi",
                difficulty_order=1,
                label="Struktur Atom",
            ),
            _fallback_node(
                node_id="km_e_biologi_sel",
                subject="biologi",
                subject_label="Biologi",
                phase="E",
                school_level="SMA",
                grade_range="10",
                domain="Sel",
                difficulty_order=1,
                label="Struktur Sel",
            ),
        ],
        "edges": [
            _fallback_edge(
                edge_id="edge_km_d_matematika_bilangan_bulat_rasional",
                from_node_id="km_d_matematika_bilangan_bulat",
                to_node_id="km_d_matematika_bilangan_rasional",
                strength=0.85,
            ),
            _fallback_edge(
                edge_id="edge_km_d_matematika_bilangan_rasional_irasional",
                from_node_id="km_d_matematika_bilangan_rasional",
                to_node_id="km_d_matematika_bilangan_irasional",
                strength=0.8,
            ),
        ],
    }


def _fallback_node(
    *,
    node_id: str,
    subject: str,
    subject_label: str,
    phase: str,
    school_level: str,
    grade_range: str,
    domain: str,
    difficulty_order: int,
    label: str,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "subject": subject,
        "subject_label": subject_label,
        "phase": phase,
        "school_level": school_level,
        "grade_range": grade_range,
        "domain": domain,
        "difficulty_order": difficulty_order,
        "label_id": label,
        "label_en": label,
        "description_id": f"Fallback seed untuk {label}.",
    }


def _fallback_edge(
    *,
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
    strength: float,
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "edge_type": "prerequisite",
        "strength": strength,
    }


def _subject_seed(
    subject_code: str,
    nodes: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> SubjectSeed:
    first_node = _sorted_nodes(nodes)[0]
    label = _localized_seed_value(
        first_node,
        "subject_label",
        "id",
        fallback=subject_code.title(),
    )
    label_en = _string(
        first_node,
        "subject_label_en",
        fallback=SUBJECT_LABEL_EN.get(subject_code, label),
    )
    phases = sorted({_string(node, "phase") for node in nodes}, key=_phase_sort_key)
    school_levels = sorted({_string(node, "school_level") for node in nodes if _string(node, "school_level")})
    groups = list(_subject_groups(nodes).values())
    graph_metadata = {
        "title": f"{label} Knowledge Map",
        "title_id": f"Peta Pengetahuan {label}",
        "title_en": f"{label_en} Knowledge Map",
        "width": (groups[-1]["x"] + 260.0) if groups else 1200.0,
        "height": _graph_height(nodes),
        "top_down": True,
        "groups": groups,
    }

    return SubjectSeed(
        code=subject_code,
        name=label,
        description=(
            f"{label} knowledge graph covering phases "
            f"{', '.join(phases)} across {len(nodes)} concepts."
        ),
        display_order=SUBJECT_DISPLAY_ORDER.get(subject_code, 999),
        metadata={
            "curriculum": metadata.get("curriculum", "kurikulum_merdeka"),
            "version": metadata.get("version"),
            "generated_at": metadata.get("generated_at"),
            "source_subject_code": _string(first_node, "subject"),
            "name_id": label,
            "name_en": label_en,
            "description_id": (
                f"Graf pengetahuan {label} yang mencakup fase "
                f"{', '.join(phases)} dengan {len(nodes)} konsep."
            ),
            "description_en": (
                f"{label_en} knowledge graph covering phases "
                f"{', '.join(phases)} across {len(nodes)} concepts."
            ),
            "phases": phases,
            "school_levels": school_levels,
            "node_count": len(nodes),
            "graph": graph_metadata,
        },
    )


def _concept_seed(
    *,
    subject_code: str,
    node: dict[str, Any],
    display_order: int,
    layout_x: float,
    layout_y: float,
    local_group_order: int,
) -> ConceptSeed:
    title = _localized_seed_value(node, "label", "id")
    id_desc = _optional_string(node, "description_id")
    en_desc = _english_description(node, fallback_title=title)
    metadata = _normalized_node_metadata(node, subject_code=subject_code)
    metadata.update(
        {
            "default_status": _preview_status(
                node,
                local_group_order=local_group_order,
            ),
            "local_group_order": local_group_order,
            "preview_status_only": True,
            "source_node_id": _string(node, "id"),
            "source_curriculum_graph": GRAPH_FILE_NAME,
        }
    )

    return ConceptSeed(
        subject_code=subject_code,
        code=_string(node, "id"),
        title=title,
        description=id_desc,
        id_desc=id_desc,
        en_desc=en_desc,
        grade_band=_grade_band(node),
        display_order=display_order,
        layout_x=layout_x,
        layout_y=layout_y,
        metadata=metadata,
    )


def _edge_seed(edge: dict[str, Any]) -> EdgeSeed:
    metadata = dict(edge)
    metadata.update(
        {
            "source_edge_id": _string(edge, "id"),
            "source_curriculum_graph": GRAPH_FILE_NAME,
        }
    )
    return EdgeSeed(
        from_code=_string(edge, "from_node_id"),
        to_code=_string(edge, "to_node_id"),
        edge_type=_string(edge, "edge_type", fallback="prerequisite"),
        weight=_float(edge.get("strength"), fallback=1.0),
        metadata=metadata,
    )


def _subject_groups(nodes: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for index, group_key in enumerate(
        sorted({_group_key(node) for node in nodes}, key=_group_sort_key)
    ):
        phase, domain = group_key
        domain_en = DOMAIN_LABEL_EN.get(domain, domain)
        groups[group_key] = {
            "label": f"Fase {phase} / {domain}" if phase else domain,
            "label_id": f"Fase {phase} / {domain}" if phase else domain,
            "label_en": f"Phase {phase} / {domain_en}" if phase else domain_en,
            "x": GROUP_X_START + (index * GROUP_X_GAP),
            "phase": phase,
            "domain": domain,
            "domain_id": domain,
            "domain_en": domain_en,
        }
    return groups


def _sorted_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        nodes,
        key=lambda node: (
            _phase_sort_key(_string(node, "phase")),
            _string(node, "domain"),
            _int(node.get("difficulty_order"), fallback=9999),
            _string(node, "label_id"),
            _string(node, "id"),
        ),
    )


def _group_key(node: dict[str, Any]) -> tuple[str, str]:
    return (
        _string(node, "phase", fallback="?"),
        _string(node, "domain", fallback="General"),
    )


def _group_sort_key(group_key: tuple[str, str]) -> tuple[int, str]:
    phase, domain = group_key
    return (_phase_sort_key(phase), domain)


def _grade_band(node: dict[str, Any]) -> str | None:
    phase = _string(node, "phase")
    school_level = _string(node, "school_level")
    grade_range = _string(node, "grade_range")
    if not phase and not school_level and not grade_range:
        return None
    return f"Fase {phase} ({school_level} {grade_range})".strip()


def _english_description(node: dict[str, Any], *, fallback_title: str) -> str | None:
    explicit = _optional_string(node, "description_en")
    if explicit:
        return explicit

    label = _english_label(node, fallback=fallback_title)
    if not label:
        return None
    phase = _string(node, "phase")
    school_level = _string(node, "school_level")
    grade_range = _string(node, "grade_range")
    domain = translate_curriculum_domain_to_english(_string(node, "domain"))
    context_parts = [
        part
        for part in (
            f"Phase {phase}" if phase else "",
            school_level,
            f"grades {grade_range}" if grade_range else "",
        )
        if part
    ]
    context = f" for {' / '.join(context_parts)}" if context_parts else ""
    domain_suffix = f" within {domain}" if domain else ""
    return f"Build understanding of {label}{domain_suffix}{context}."


def _preview_status(node: dict[str, Any], *, local_group_order: int) -> str:
    phase = _string(node, "phase")
    difficulty = local_group_order

    if phase in {"A", "B"}:
        return "mastered"
    if phase == "C":
        return "review" if difficulty % 5 == 0 else "ready"
    if phase == "D":
        if difficulty <= 2:
            return "active"
        if difficulty % 7 == 0:
            return "gap"
        if difficulty % 4 == 0:
            return "review"
        return "ready"
    if phase == "E":
        if difficulty <= 2:
            return "active"
        return "gap" if difficulty % 6 == 0 else "locked"
    if phase == "F":
        return "ready" if difficulty == 1 else "locked"
    return "ready"


def _graph_height(nodes: list[dict[str, Any]]) -> float:
    group_counts: dict[tuple[str, str], int] = {}
    for node in nodes:
        key = _group_key(node)
        group_counts[key] = group_counts.get(key, 0) + 1
    max_group_count = max(group_counts.values(), default=6)
    return max(600.0, NODE_Y_START + (max_group_count * NODE_Y_GAP) + 80.0)


def _phase_sort_key(phase: str) -> int:
    return PHASE_ORDER.get(phase, 999)


def _slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _string(
    payload: dict[str, Any],
    key: str,
    *,
    fallback: str = "",
) -> str:
    value = payload.get(key)
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = _string(payload, key)
    return value or None


def _int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _float(value: Any, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _localized_seed_value(
    payload: dict[str, Any],
    base_key: str,
    locale: str,
    *,
    fallback: str = "",
) -> str:
    locale_key = f"{base_key}_{locale}"
    id_key = f"{base_key}_id"
    return _string(
        payload,
        locale_key,
        fallback=_string(
            payload,
            id_key,
            fallback=_string(payload, base_key, fallback=fallback),
        ),
    )


def _english_label(payload: dict[str, Any], *, fallback: str = "") -> str:
    label_id = _localized_seed_value(payload, "label", "id", fallback=fallback)
    explicit = _optional_string(payload, "label_en")
    if explicit and explicit.casefold() != label_id.casefold():
        return explicit
    return _translate_indonesian_label(label_id or fallback)


def _translate_indonesian_label(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    normalized = " ".join(text.lower().replace("-", " ").split())
    if normalized in LABEL_PHRASE_EN:
        return _sentence_case(LABEL_PHRASE_EN[normalized])

    translated = normalized
    for source, target in sorted(
        LABEL_PHRASE_EN.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        translated = translated.replace(source, target)

    words = [
        LABEL_WORD_EN.get(word, word.upper() if word.isupper() else word)
        for word in translated.split()
    ]
    return _sentence_case(" ".join(words))


def translate_curriculum_label_to_english(value: str) -> str:
    return _translate_indonesian_label(value)


def translate_curriculum_domain_to_english(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return DOMAIN_LABEL_EN.get(text, _translate_indonesian_label(text))


def _sentence_case(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _normalized_node_metadata(
    node: dict[str, Any],
    *,
    subject_code: str,
) -> dict[str, Any]:
    metadata = dict(node)
    subject_label_id = _localized_seed_value(
        node,
        "subject_label",
        "id",
        fallback=subject_code.title(),
    )
    domain_id = _localized_seed_value(node, "domain", "id", fallback="General")
    element_id = _localized_seed_value(node, "element", "id", fallback=domain_id)
    label_id = _localized_seed_value(node, "label", "id")
    description_id = _localized_seed_value(node, "description", "id")

    metadata.setdefault("subject_label_id", subject_label_id)
    metadata.setdefault(
        "subject_label_en",
        _string(
            node,
            "subject_label_en",
            fallback=SUBJECT_LABEL_EN.get(subject_code, subject_label_id),
        ),
    )
    metadata.setdefault("domain_id", domain_id)
    metadata.setdefault("domain_en", DOMAIN_LABEL_EN.get(domain_id, domain_id))
    metadata.setdefault("element_id", element_id)
    metadata.setdefault("element_en", DOMAIN_LABEL_EN.get(element_id, element_id))
    metadata.setdefault("label_id", label_id)
    metadata["label_en"] = _english_label(node, fallback=label_id)
    if description_id:
        metadata.setdefault("description_id", description_id)
    metadata["description_en"] = (
        _english_description(node, fallback_title=label_id) or ""
    )
    if "concept_visual_pattern" in metadata:
        metadata.setdefault("concept_visual_pattern_en", metadata["concept_visual_pattern"])
    if "real_world_anchor_examples" in metadata:
        metadata.setdefault(
            "real_world_anchor_examples_id",
            metadata["real_world_anchor_examples"],
        )
    metadata.setdefault(
        "translation_status",
        {
            "id": "source",
            "en": "machine_draft",
            "teacher_review_required": True,
        },
    )
    return metadata
