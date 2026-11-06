# I use built-in Python packages; no installation is required.
import sqlite3
import random
from pathlib import Path
from datetime import date, timedelta

# I fix the seed to make the data generation reproducible.
rng = random.Random(42)
db_path = Path("mattress_testing.db")

# Rerunning this cell replaces only this generated database.
if db_path.exists():
    db_path.unlink()

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA foreign_keys = ON")

# ==========================================================
# 1. CREATE FIVE RELATED TABLES
# ==========================================================
# Measurement types:
# Nominal: foam_type.
# Ordinal: firmness_level (Soft < Medium < Firm < Extra firm).
# Interval: test_temperature_c.
# Ratio: thickness_cm, compression_cycles, indentation_depth_mm.
#
# The protocols below are fictional laboratory scenarios.
# They do not claim compliance with a published testing standard.

conn.executescript("""
CREATE TABLE mattress_models (
    model_id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL UNIQUE
        CHECK (length(trim(model_name)) > 0),
    firmness_level TEXT NOT NULL
        CHECK (firmness_level IN
            ('Soft', 'Medium', 'Firm', 'Extra firm')),
    mattress_size TEXT NOT NULL
        CHECK (mattress_size IN ('Single', 'Double', 'King')),
    width_cm REAL NOT NULL CHECK (width_cm BETWEEN 80 AND 200),
    length_cm REAL NOT NULL CHECK (length_cm BETWEEN 180 AND 220),

    CHECK (
        (mattress_size = 'Single' AND width_cm = 90 AND length_cm = 190)
        OR
        (mattress_size = 'Double' AND width_cm = 135 AND length_cm = 190)
        OR
        (mattress_size = 'King' AND width_cm = 150 AND length_cm = 200)
    )
);

CREATE TABLE construction_layers (
    model_id INTEGER NOT NULL,
    layer_number INTEGER NOT NULL
        CHECK (layer_number BETWEEN 1 AND 4),
    layer_role TEXT NOT NULL
        CHECK (layer_role IN
            ('Comfort', 'Transition', 'Support', 'Base')),
    foam_type TEXT NOT NULL
        CHECK (foam_type IN
            ('Memory foam', 'Latex foam', 'Polyurethane foam')),
    thickness_cm REAL NOT NULL
        CHECK (thickness_cm BETWEEN 1 AND 20),
    density_kg_m3 REAL NOT NULL
        CHECK (density_kg_m3 BETWEEN 20 AND 100),

    PRIMARY KEY (model_id, layer_number),
    FOREIGN KEY (model_id) REFERENCES mattress_models(model_id),

    CHECK (
        (layer_number = 1 AND layer_role = 'Comfort')
        OR (layer_number = 2 AND layer_role = 'Transition')
        OR (layer_number = 3 AND layer_role = 'Support')
        OR (layer_number = 4 AND layer_role = 'Base')
    )
);

CREATE TABLE test_protocols (
    protocol_id INTEGER PRIMARY KEY,
    protocol_name TEXT NOT NULL UNIQUE,
    applied_load_n REAL NOT NULL
        CHECK (applied_load_n BETWEEN 300 AND 1500),
    target_cycles INTEGER NOT NULL
        CHECK (target_cycles > 0),
    checkpoint_interval INTEGER NOT NULL
        CHECK (checkpoint_interval > 0),
    recovery_minutes INTEGER NOT NULL
        CHECK (recovery_minutes BETWEEN 10 AND 120),

    CHECK (target_cycles = checkpoint_interval * 7)
);

CREATE TABLE test_runs (
    run_id INTEGER PRIMARY KEY,
    model_id INTEGER NOT NULL,
    protocol_id INTEGER NOT NULL,
    specimen_code TEXT NOT NULL UNIQUE,
    test_date TEXT NOT NULL
        CHECK (
            length(test_date) = 10
            AND date(test_date, '+0 days') IS NOT NULL
            AND date(test_date, '+0 days') = test_date
            AND test_date BETWEEN '2025-01-01' AND '2025-12-31'
        ),
    test_temperature_c REAL NOT NULL
        CHECK (test_temperature_c BETWEEN 16 AND 32),
    relative_humidity_pct REAL
        CHECK (
            relative_humidity_pct IS NULL
            OR relative_humidity_pct BETWEEN 20 AND 80
        ),
    operator_code TEXT NOT NULL
        CHECK (operator_code IN ('LAB01', 'LAB02', 'LAB03', 'LAB04')),
    run_notes TEXT
        CHECK (run_notes IS NULL OR length(trim(run_notes)) > 0),

    FOREIGN KEY (model_id) REFERENCES mattress_models(model_id),
    FOREIGN KEY (protocol_id) REFERENCES test_protocols(protocol_id)
);

CREATE TABLE observations (
    run_id INTEGER NOT NULL,
    observation_number INTEGER NOT NULL
        CHECK (observation_number > 0),
    compression_cycles INTEGER NOT NULL
        CHECK (compression_cycles >= 0),
    indentation_depth_mm REAL NOT NULL
        CHECK (indentation_depth_mm BETWEEN 0 AND 50),
    measurement_method TEXT NOT NULL
        CHECK (measurement_method = 'Residual indentation after recovery'),
    observation_notes TEXT NOT NULL
        CHECK (length(trim(observation_notes)) > 0),

    PRIMARY KEY (run_id, observation_number),
    FOREIGN KEY (run_id) REFERENCES test_runs(run_id)
);

-- SQLite CHECK constraints cannot query another table.
-- These triggers enforce the protocol's checkpoint rules.
CREATE TRIGGER observation_protocol_insert
BEFORE INSERT ON observations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Invalid protocol checkpoint')
    WHERE NOT EXISTS (
        SELECT 1
        FROM test_runs AS r
        JOIN test_protocols AS p ON p.protocol_id = r.protocol_id
        WHERE r.run_id = NEW.run_id
          AND NEW.compression_cycles <= p.target_cycles
          AND NEW.compression_cycles % p.checkpoint_interval = 0
    );
END;

CREATE TRIGGER observation_protocol_update
BEFORE UPDATE ON observations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'Invalid protocol checkpoint')
    WHERE NOT EXISTS (
        SELECT 1
        FROM test_runs AS r
        JOIN test_protocols AS p ON p.protocol_id = r.protocol_id
        WHERE r.run_id = NEW.run_id
          AND NEW.compression_cycles <= p.target_cycles
          AND NEW.compression_cycles % p.checkpoint_interval = 0
    );
END;
""")

# ==========================================================
# 2. GENERATE 30 FICTIONAL MATTRESS MODELS
# ==========================================================
sizes = {
    "Single": (90, 190),
    "Double": (135, 190),
    "King": (150, 200)
}

models = []
firmness_by_model = {}

for model_id in range(1, 31):
    firmness = rng.choices(
        ["Soft", "Medium", "Firm", "Extra firm"],
        weights=[15, 40, 35, 10]
    )[0]

    size = rng.choices(
        list(sizes), weights=[25, 45, 30]
    )[0]
    width, length = sizes[size]

    models.append((
        model_id,
        f"RestLab Model {model_id:02d}",
        firmness,
        size,
        width,
        length
    ))
    firmness_by_model[model_id] = firmness

conn.executemany(
    "INSERT INTO mattress_models VALUES (?, ?, ?, ?, ?, ?)",
    models
)

# ==========================================================
# 3. GENERATE FOUR LAYERS PER MODEL: 120 ROWS
# ==========================================================
# Layers are numbered from the sleeping surface downwards.
# Support layers are thicker than comfort and base layers.
layers = []
model_properties = {}

for model_id in range(1, 31):
    comfort_foam = rng.choices(
        ["Memory foam", "Latex foam", "Polyurethane foam"],
        weights=[45, 30, 25]
    )[0]

    comfort_density = {
        "Memory foam": rng.uniform(45, 75),
        "Latex foam": rng.uniform(60, 90),
        "Polyurethane foam": rng.uniform(25, 40)
    }[comfort_foam]

    support_density = rng.uniform(30, 48)

    specifications = [
        ("Comfort", comfort_foam,
         rng.triangular(3, 6, 4), comfort_density),
        ("Transition", "Polyurethane foam",
         rng.triangular(2, 5, 3), rng.uniform(28, 42)),
        ("Support", "Polyurethane foam",
         rng.triangular(10, 16, 13), support_density),
        ("Base", "Polyurethane foam",
         rng.triangular(2, 4, 3), rng.uniform(25, 38))
    ]

    for layer_number, (role, foam, thickness, density) in enumerate(
        specifications, start=1
    ):
        layers.append((
            model_id, layer_number, role, foam,
            round(thickness, 2), round(density, 2)
        ))

    model_properties[model_id] = (
        comfort_foam, comfort_density, support_density
    )

conn.executemany(
    "INSERT INTO construction_layers VALUES (?, ?, ?, ?, ?, ?)",
    layers
)

# ==========================================================
# 4. CREATE FIVE FICTIONAL TEST PROTOCOLS
# ==========================================================
# Each protocol has eight checkpoints, including cycle zero.
protocols = [
    (1, "Light repeated loading", 450, 21000, 3000, 30),
    (2, "Routine repeated loading", 600, 35000, 5000, 30),
    (3, "Extended repeated loading", 750, 56000, 8000, 45),
    (4, "Heavy repeated loading", 1000, 70000, 10000, 60),
    (5, "High-load endurance", 1200, 84000, 12000, 60)
]

conn.executemany(
    "INSERT INTO test_protocols VALUES (?, ?, ?, ?, ?, ?)",
    protocols
)

# ==========================================================
# 5. GENERATE 200 TEST RUNS
# ==========================================================
# Every run uses a different fictional physical specimen.
# I ensure every mattress model appears in at least one run.
model_schedule = list(range(1, 31))
model_schedule += rng.choices(
    range(1, 31),
    weights=[rng.uniform(0.5, 2.5) for _ in range(30)],
    k=170
)
rng.shuffle(model_schedule)

# I deliberately omit 10 humidity readings: 5% of runs.
# I deliberately omit 20 run notes: 10% of runs.
missing_humidity = set(rng.sample(range(1, 201), 10))
missing_run_notes = set(rng.sample(range(1, 201), 20))

runs = []
run_settings = {}

for run_id, model_id in enumerate(model_schedule, start=1):
    protocol = rng.choices(
        protocols, weights=[15, 35, 25, 15, 10]
    )[0]

    temperature = round(
        rng.triangular(18, 30, 23), 1
    )

    humidity = (
        None if run_id in missing_humidity
        else round(rng.triangular(30, 70, 50), 1)
    )

    tested_on = date(2025, 1, 1) + timedelta(
        days=rng.randrange(365)
    )

    notes = (
        None if run_id in missing_run_notes
        else "Conditioned specimen; controlled laboratory test"
    )

    runs.append((
        run_id, model_id, protocol[0],
        f"SPEC-2025-{run_id:04d}",
        tested_on.isoformat(), temperature, humidity,
        rng.choice(["LAB01", "LAB02", "LAB03", "LAB04"]),
        notes
    ))

    run_settings[run_id] = (model_id, protocol, temperature)

conn.executemany(
    "INSERT INTO test_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    runs
)

# ==========================================================
# 6. GENERATE 1,600 ORIGINAL OBSERVATIONS
# ==========================================================
# I simulate residual indentation after the protocol's recovery.
# These are transparent modelling assumptions, not real findings.
# Load and accumulated cycles increase simulated deformation.
# Material, density, firmness and recovery time modify its extent.
firmness_factor = {
    "Soft": 1.15,
    "Medium": 1.00,
    "Firm": 0.90,
    "Extra firm": 0.82
}

material_factor = {
    "Memory foam": 1.00,
    "Latex foam": 0.80,
    "Polyurethane foam": 1.15
}

original_observations = []

for run_id in range(1, 201):
    model_id, protocol, temperature = run_settings[run_id]
    _, _, load, target, interval, recovery = protocol
    foam, comfort_density, support_density = model_properties[model_id]

    # I include specimen-to-specimen variation.
    specimen_factor = rng.lognormvariate(0, 0.10)

    # Temperature sensitivity differs between foam types.
    temperature_sensitivity = {
        "Memory foam": 0.025,
        "Latex foam": 0.008,
        "Polyurethane foam": 0.012
    }[foam]

    temperature_factor = (
        1 + temperature_sensitivity * (temperature - 23)
    )

    density_factor = (
        (55 / comfort_density) ** 0.15
        * (38 / support_density) ** 0.25
    )

    final_indentation = (
        5.5
        * (load / 600) ** 0.65
        * (target / 35000) ** 0.50
        * material_factor[foam]
        * firmness_factor[firmness_by_model[model_id]]
        * temperature_factor
        * density_factor
        * (30 / recovery) ** 0.12
        * specimen_factor
    )

    for checkpoint in range(8):
        cycles = checkpoint * interval

        if checkpoint == 0:
            indentation = 0.0
            note = "Baseline reference before repeated loading"
        else:
            expected = final_indentation * (cycles / target) ** 0.65
            indentation = round(
                max(0, expected + rng.gauss(0, 0.12)), 2
            )
            note = f"Measured after {recovery}-minute recovery"

        original_observations.append((
            run_id, checkpoint + 1, cycles, indentation,
            "Residual indentation after recovery", note
        ))

conn.executemany(
    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
    original_observations
)

# ==========================================================
# 7. DELIBERATELY INSERT 16 DUPLICATE COPIES
# ==========================================================
# A duplicate matches all observation business fields except
# observation_number. It represents accidental repeated entry.
# I select different runs so each copied record can use number 9.
duplicate_runs = rng.sample(range(1, 201), 16)
duplicate_records = []

for run_id in duplicate_runs:
    checkpoint_number = rng.randint(2, 8)
    source = original_observations[
        (run_id - 1) * 8 + checkpoint_number - 1
    ]

    duplicate_records.append((
        source[0], 9, source[2], source[3], source[4], source[5]
    ))

conn.executemany(
    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
    duplicate_records
)
conn.commit()

# ==========================================================
# 8. DISPLAY ROW COUNTS AND TABLE STRUCTURES
# ==========================================================
expected_counts = {
    "mattress_models": 30,
    "construction_layers": 120,
    "test_protocols": 5,
    "test_runs": 200,
    "observations": 1616
}

print("DATABASE TABLES")
for table, expected in expected_counts.items():
    actual = conn.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]
    assert actual == expected

    print(f"\n{table}: {actual:,} rows")

    for column in conn.execute(f"PRAGMA table_info({table})"):
        _, name, dtype, required, default, key_position = column
        print(
            f"  {name}: {dtype}; NOT NULL={bool(required)}; "
            f"PK position={key_position}"
        )

    print(
        "  Foreign keys:",
        conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    )

assert len(conn.execute(
    "PRAGMA table_info(test_runs)"
).fetchall()) == 9

# ==========================================================
# 9. DISPLAY ACTUAL MISSING AND DUPLICATE COUNTS
# ==========================================================
print("\nDELIBERATE MISSING VALUES")

for column, expected in [
    ("relative_humidity_pct", 10),
    ("run_notes", 20)
]:
    missing = conn.execute(
        f"SELECT COUNT(*) FROM test_runs WHERE {column} IS NULL"
    ).fetchone()[0]

    assert missing == expected
    print(f"{column}: {missing}/200 = {missing / 200:.2%}")

groups, copies = conn.execute("""
    SELECT COUNT(*), COALESCE(SUM(n - 1), 0)
    FROM (
        SELECT COUNT(*) AS n
        FROM observations
        GROUP BY run_id, compression_cycles,
                 indentation_depth_mm, measurement_method,
                 observation_notes
        HAVING COUNT(*) > 1
    )
""").fetchone()

assert groups == 16
assert copies == 16

print("\nDELIBERATE DUPLICATES")
print("Duplicate groups:", groups)
print("Original observations: 1,600")
print("Additional duplicate copies:", copies)
print(f"Copy percentage of final observations: {copies / 1616:.2%}")

# ==========================================================
# 10. VALIDATE INTEGRITY AND BUSINESS RELATIONSHIPS
# ==========================================================
assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

# Every mattress has four layers.
assert conn.execute("""
    SELECT COUNT(*)
    FROM (
        SELECT m.model_id
        FROM mattress_models AS m
        LEFT JOIN construction_layers AS l ON l.model_id = m.model_id
        GROUP BY m.model_id
        HAVING COUNT(l.layer_number) <> 4
    )
""").fetchone()[0] == 0

# Every run has all eight distinct checkpoints.
assert conn.execute("""
    SELECT COUNT(*)
    FROM (
        SELECT run_id
        FROM observations
        GROUP BY run_id
        HAVING COUNT(DISTINCT compression_cycles) <> 8
    )
""").fetchone()[0] == 0

# All checkpoints agree with their referenced protocol.
assert conn.execute("""
    SELECT COUNT(*)
    FROM observations AS o
    JOIN test_runs AS r ON r.run_id = o.run_id
    JOIN test_protocols AS p ON p.protocol_id = r.protocol_id
    WHERE o.compression_cycles > p.target_cycles
       OR o.compression_cycles % p.checkpoint_interval <> 0
""").fetchone()[0] == 0

# Residual indentation must be below the mattress's total thickness.
assert conn.execute("""
    SELECT COUNT(*)
    FROM observations AS o
    JOIN test_runs AS r ON r.run_id = o.run_id
    JOIN (
        SELECT model_id, SUM(thickness_cm) * 10 AS thickness_mm
        FROM construction_layers
        GROUP BY model_id
    ) AS t ON t.model_id = r.model_id
    WHERE o.indentation_depth_mm > t.thickness_mm
""").fetchone()[0] == 0

# I test constraints using savepoints and discard test records.
def expect_rejection(label, sql, values):
    conn.execute("SAVEPOINT constraint_test")
    rejected = False
    try:
        conn.execute(sql, values)
    except sqlite3.IntegrityError:
        rejected = True
    finally:
        conn.execute("ROLLBACK TO constraint_test")
        conn.execute("RELEASE constraint_test")

    assert rejected, f"Failed constraint test: {label}"
    print("Passed:", label)

expect_rejection(
    "Composite layer primary key",
    "INSERT INTO construction_layers VALUES (?, ?, ?, ?, ?, ?)",
    layers[0]
)

expect_rejection(
    "Composite observation primary key",
    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
    original_observations[0]
)

bad_layer = list(layers[0])
bad_layer[0] = 999

expect_rejection(
    "Foreign-key enforcement",
    "INSERT INTO construction_layers VALUES (?, ?, ?, ?, ?, ?)",
    bad_layer
)

bad_layer = list(layers[0])
bad_layer[0] = 1
bad_layer[1] = 5
bad_layer[4] = -2

expect_rejection(
    "Layer position and positive thickness",
    "INSERT INTO construction_layers VALUES (?, ?, ?, ?, ?, ?)",
    bad_layer
)

bad_run = list(runs[0])
bad_run[0] = 999

expect_rejection(
    "Unique specimen code",
    "INSERT INTO test_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    bad_run
)

bad_run[3] = "VALIDATION-SPECIMEN"
bad_run[5] = None

expect_rejection(
    "Required temperature",
    "INSERT INTO test_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    bad_run
)

expect_rejection(
    "Protocol checkpoint rule",
    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
    (
        1, 99, 1, 0.5,
        "Residual indentation after recovery",
        "Validation record"
    )
)

conn.commit()
conn.close()

print("\nAll checks passed.")
print("Database:", db_path.resolve())
print(f"File size: {db_path.stat().st_size:,} bytes")

# ==========================================================
# 11. DOWNLOAD THE DATABASE
# ==========================================================
try:
    from google.colab import files
except ImportError:
    print("Outside Colab: the database is saved in the working folder.")
else:
    files.download(str(db_path))