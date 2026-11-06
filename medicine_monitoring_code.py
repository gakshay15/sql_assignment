"""
MEDICINE BATCH AND STORAGE MONITORING
Google Colab — no additional package installation required.

Six linked tables:
    manufacturers
    medicines
    batches
    storage_units
    batch_storage
    temperature_readings

All data and storage requirements are fictional.

Measurement types:
    Nominal: dosage_form
    Ordinal: review_priority — Low < Medium < High
    Interval: temperature_c
    Ratio: quantity_available, measured in packs

Deliberate data imperfections:
    2 missing manufacturer emails
    180 missing humidity measurements
    60 extra duplicate temperature events

Composite primary keys:
    batch_storage: (batch_id, storage_unit_id)
    temperature_readings: (storage_unit_id, reading_sequence)
"""

import math
import random
import sqlite3
import tempfile

from datetime import datetime, timedelta
from pathlib import Path
from google.colab import files


# ============================================================
# 1. Configuration
# ============================================================

SEED = 3398
rng = random.Random(SEED)

START_DATE = datetime(2026, 1, 1)
END_DATE = START_DATE + timedelta(hours=499)

MANUFACTURER_COUNT = 15
MEDICINE_COUNT = 60
BATCH_COUNT = 300
STORAGE_UNIT_COUNT = 12
READINGS_PER_UNIT = 500

MISSING_EMAIL_COUNT = 2
MISSING_HUMIDITY_COUNT = 180
DUPLICATE_COUNT = 60

# Create a new folder each run, preserving previous databases.
OUTPUT_DIR = Path(
    tempfile.mkdtemp(prefix="medicine_", dir="/content")
)
DATABASE_PATH = OUTPUT_DIR / "medicine_monitoring.db"


# ============================================================
# 2. Create the SQLite schema
# ============================================================

SCHEMA = """
CREATE TABLE manufacturers (
    manufacturer_id INTEGER PRIMARY KEY,

    manufacturer_name TEXT NOT NULL UNIQUE
        CHECK (length(trim(manufacturer_name)) > 0),

    country TEXT NOT NULL
        CHECK (country IN ('UK', 'India', 'Germany', 'Ireland')),

    contact_email TEXT
        CHECK (
            contact_email IS NULL
            OR contact_email LIKE '%@example.com'
        )
);

CREATE TABLE medicines (
    medicine_id INTEGER PRIMARY KEY,

    medicine_name TEXT NOT NULL UNIQUE
        CHECK (length(trim(medicine_name)) > 0),

    dosage_form TEXT NOT NULL
        CHECK (
            dosage_form IN ('Tablet', 'Capsule', 'Liquid', 'Injection')
        ),

    strength_mg_per_unit REAL NOT NULL
        CHECK (
            typeof(strength_mg_per_unit) IN ('integer', 'real')
            AND strength_mg_per_unit > 0
        ),

    units_per_pack INTEGER NOT NULL
        CHECK (
            typeof(units_per_pack) = 'integer'
            AND units_per_pack BETWEEN 1 AND 1000
        ),

    storage_min_c REAL NOT NULL
        CHECK (storage_min_c BETWEEN -20 AND 40),

    storage_max_c REAL NOT NULL
        CHECK (
            storage_max_c BETWEEN -20 AND 40
            AND storage_max_c > storage_min_c
        )
);

CREATE TABLE batches (
    batch_id INTEGER PRIMARY KEY,

    medicine_id INTEGER NOT NULL,
    manufacturer_id INTEGER NOT NULL,

    batch_number TEXT NOT NULL
        CHECK (length(trim(batch_number)) > 0),

    manufactured_on TEXT NOT NULL
        CHECK (__MANUFACTURE_DATE__),

    expires_on TEXT NOT NULL
        CHECK (
            __EXPIRY_DATE__
            AND expires_on > manufactured_on
        ),

    quantity_received_packs INTEGER NOT NULL
        CHECK (
            typeof(quantity_received_packs) = 'integer'
            AND quantity_received_packs > 0
        ),

    unit_cost_pence INTEGER NOT NULL
        CHECK (
            typeof(unit_cost_pence) = 'integer'
            AND unit_cost_pence > 0
        ),

    UNIQUE (manufacturer_id, batch_number),

    FOREIGN KEY (medicine_id)
        REFERENCES medicines(medicine_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,

    FOREIGN KEY (manufacturer_id)
        REFERENCES manufacturers(manufacturer_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE storage_units (
    storage_unit_id INTEGER PRIMARY KEY,

    unit_name TEXT NOT NULL UNIQUE
        CHECK (length(trim(unit_name)) > 0),

    location TEXT NOT NULL
        CHECK (
            location IN ('North depot', 'Central depot', 'South depot')
        ),

    unit_type TEXT NOT NULL
        CHECK (unit_type IN ('Refrigerated', 'Ambient')),

    capacity_packs INTEGER NOT NULL
        CHECK (
            typeof(capacity_packs) = 'integer'
            AND capacity_packs > 0
        ),

    operating_min_c REAL NOT NULL,
    operating_max_c REAL NOT NULL,

    CHECK (
        (
            unit_type = 'Refrigerated'
            AND operating_min_c = 2
            AND operating_max_c = 8
        )
        OR
        (
            unit_type = 'Ambient'
            AND operating_min_c = 15
            AND operating_max_c = 25
        )
    )
);

CREATE TABLE batch_storage (
    batch_id INTEGER NOT NULL,
    storage_unit_id INTEGER NOT NULL,

    quantity_available INTEGER NOT NULL
        CHECK (
            typeof(quantity_available) = 'integer'
            AND quantity_available > 0
        ),

    placed_on TEXT NOT NULL
        CHECK (__PLACEMENT_DATE__),

    PRIMARY KEY (batch_id, storage_unit_id),

    FOREIGN KEY (batch_id)
        REFERENCES batches(batch_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,

    FOREIGN KEY (storage_unit_id)
        REFERENCES storage_units(storage_unit_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE temperature_readings (
    storage_unit_id INTEGER NOT NULL,

    reading_sequence INTEGER NOT NULL
        CHECK (
            typeof(reading_sequence) = 'integer'
            AND reading_sequence > 0
        ),

    recorded_at TEXT NOT NULL
        CHECK (
            length(recorded_at) = 19
            AND recorded_at GLOB
                '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]'
            AND datetime(recorded_at, '+0 days') IS NOT NULL
            AND datetime(recorded_at, '+0 days') = recorded_at
        ),

    temperature_c REAL NOT NULL
        CHECK (
            typeof(temperature_c) IN ('integer', 'real')
            AND temperature_c BETWEEN -30 AND 60
        ),

    humidity_percent REAL
        CHECK (
            humidity_percent IS NULL
            OR (
                typeof(humidity_percent) IN ('integer', 'real')
                AND humidity_percent BETWEEN 0 AND 100
            )
        ),

    recording_method TEXT NOT NULL
        CHECK (recording_method IN ('Automatic', 'Manual')),

    review_priority TEXT NOT NULL
        CHECK (review_priority IN ('Low', 'Medium', 'High')),

    note TEXT
        CHECK (note IS NULL OR length(trim(note)) > 0),

    PRIMARY KEY (storage_unit_id, reading_sequence),

    FOREIGN KEY (storage_unit_id)
        REFERENCES storage_units(storage_unit_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_batches_medicine ON batches(medicine_id);
CREATE INDEX idx_batches_manufacturer ON batches(manufacturer_id);
CREATE INDEX idx_batches_expiry ON batches(expires_on);
CREATE INDEX idx_stock_unit ON batch_storage(storage_unit_id);

CREATE INDEX idx_temperature_time
    ON temperature_readings(storage_unit_id, recorded_at);
"""


def date_constraint(column):
    """Require valid ISO calendar dates."""
    return f"""
        length({column}) = 10
        AND {column} GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        AND date({column}, '+0 days') IS NOT NULL
        AND date({column}, '+0 days') = {column}
    """


SCHEMA = (
    SCHEMA
    .replace(
        "__MANUFACTURE_DATE__",
        date_constraint("manufactured_on")
    )
    .replace(
        "__EXPIRY_DATE__",
        date_constraint("expires_on")
    )
    .replace(
        "__PLACEMENT_DATE__",
        date_constraint("placed_on")
    )
)


# ============================================================
# 3. Business-rule triggers
# ============================================================

# The view identifies cross-table violations.
# These rules cannot be enforced by ordinary column CHECK clauses.

BUSINESS_RULES = """
CREATE VIEW allocation_rule_violations AS

SELECT 'Batch stock exceeded' AS violation
FROM batches b
JOIN batch_storage a ON a.batch_id = b.batch_id
GROUP BY b.batch_id
HAVING SUM(a.quantity_available) > b.quantity_received_packs

UNION ALL

SELECT 'Storage capacity exceeded'
FROM storage_units s
JOIN batch_storage a ON a.storage_unit_id = s.storage_unit_id
GROUP BY s.storage_unit_id
HAVING SUM(a.quantity_available) > s.capacity_packs

UNION ALL

SELECT 'Incompatible storage range'
FROM batch_storage a
JOIN batches b ON b.batch_id = a.batch_id
JOIN medicines m ON m.medicine_id = b.medicine_id
JOIN storage_units s ON s.storage_unit_id = a.storage_unit_id
WHERE s.operating_min_c < m.storage_min_c
   OR s.operating_max_c > m.storage_max_c

UNION ALL

SELECT 'Invalid stock placement date'
FROM batch_storage a
JOIN batches b ON b.batch_id = a.batch_id
WHERE a.placed_on < b.manufactured_on
   OR a.placed_on >= b.expires_on;
"""


def create_business_triggers(connection):
    """
    Protect allocation rules during inserts and updates,
    including changes to parent records.
    """
    for table in (
        "batch_storage",
        "batches",
        "storage_units",
        "medicines",
    ):
        for operation in ("INSERT", "UPDATE"):
            trigger_name = f"validate_{table}_{operation.lower()}"

            connection.execute(f"""
                CREATE TRIGGER {trigger_name}
                AFTER {operation} ON {table}
                BEGIN
                    SELECT CASE WHEN EXISTS (
                        SELECT 1 FROM allocation_rule_violations
                    )
                    THEN RAISE(
                        ABORT,
                        'Inventory allocation business rule violated'
                    )
                    END;
                END;
            """)


# ============================================================
# 4. Generate fictional manufacturers
# ============================================================

manufacturer_names = [
    "Alder", "Birch", "Cedar", "Dune", "Elm",
    "Fern", "Grove", "Hazel", "Iris", "Juniper",
    "Kestrel", "Linden", "Maple", "Northfield", "Orchard"
]

missing_email_ids = set(
    rng.sample(
        range(1, MANUFACTURER_COUNT + 1),
        MISSING_EMAIL_COUNT
    )
)

manufacturers = []

for manufacturer_id, name in enumerate(manufacturer_names, start=1):
    email = (
        None
        if manufacturer_id in missing_email_ids
        else f"contact.{name.lower()}@example.com"
    )

    manufacturers.append((
        manufacturer_id,
        f"Fictional {name} Laboratories",
        rng.choices(
            ["UK", "India", "Germany", "Ireland"],
            weights=[4, 3, 2, 1],
            k=1
        )[0],
        email
    ))


# ============================================================
# 5. Generate fictional medicines
# ============================================================

medicines = []
chilled_medicine_ids = set()

for medicine_id in range(1, MEDICINE_COUNT + 1):
    dosage_form = rng.choices(
        ["Tablet", "Capsule", "Liquid", "Injection"],
        weights=[45, 25, 20, 10],
        k=1
    )[0]

    chilled = (
        dosage_form == "Injection"
        or (
            dosage_form == "Liquid"
            and rng.random() < 0.35
        )
    )

    if chilled:
        storage_min, storage_max = 2.0, 8.0
        chilled_medicine_ids.add(medicine_id)
    else:
        storage_min, storage_max = 15.0, 25.0

    # Unit means one tablet/capsule, or one mL for liquid/injection.
    units_per_pack = rng.choice(
        [10, 20, 30, 60]
        if dosage_form in ("Tablet", "Capsule")
        else [5, 10, 50, 100]
    )

    medicines.append((
        medicine_id,
        f"SimMed-{medicine_id:03d}",
        dosage_form,
        float(rng.choice([5, 10, 20, 50, 100, 250, 500])),
        units_per_pack,
        storage_min,
        storage_max
    ))


# ============================================================
# 6. Generate storage units
# ============================================================

storage_units = []

for unit_id in range(1, STORAGE_UNIT_COUNT + 1):
    chilled = unit_id <= 4

    storage_units.append((
        unit_id,
        f"{'Cold' if chilled else 'Ambient'}-{unit_id:02d}",
        ["North depot", "Central depot", "South depot"][
            (unit_id - 1) % 3
        ],
        "Refrigerated" if chilled else "Ambient",
        20000 if chilled else 30000,
        2.0 if chilled else 15.0,
        8.0 if chilled else 25.0
    ))


# ============================================================
# 7. Generate batches and current stock allocations
# ============================================================

batches = []
allocations = []

medicine_ids = list(range(1, MEDICINE_COUNT + 1))

# Unequal popularity gives some medicines more production batches.
medicine_weights = [
    rng.lognormvariate(0, 0.8)
    for _ in medicine_ids
]

manufacturer_weights = [
    rng.uniform(0.5, 2.5)
    for _ in range(MANUFACTURER_COUNT)
]

for batch_id in range(1, BATCH_COUNT + 1):
    # Guarantee that every medicine has at least one batch.
    medicine_id = (
        batch_id
        if batch_id <= MEDICINE_COUNT
        else rng.choices(
            medicine_ids,
            weights=medicine_weights,
            k=1
        )[0]
    )

    manufacturer_id = rng.choices(
        range(1, MANUFACTURER_COUNT + 1),
        weights=manufacturer_weights,
        k=1
    )[0]

    manufactured_on = START_DATE - timedelta(
        days=rng.randint(30, 300)
    )

    expires_on = manufactured_on + timedelta(
        days=rng.choice([365, 540, 730])
    )

    # Positive skew produces varied batch quantities.
    received_packs = max(
        100,
        min(
            1500,
            int(rng.lognormvariate(math.log(500), 0.45))
        )
    )

    available_packs = max(
        1,
        int(received_packs * rng.uniform(0.35, 0.95))
    )

    chilled = medicine_id in chilled_medicine_ids

    # Money is stored in integer pence to avoid floating-point errors.
    unit_cost_pence = max(
        50,
        min(
            20000,
            int(
                rng.lognormvariate(
                    math.log(1800 if chilled else 650),
                    0.55
                )
            )
        )
    )

    batches.append((
        batch_id,
        medicine_id,
        manufacturer_id,
        f"SIM-{manufacturer_id:02d}-{batch_id:05d}",
        manufactured_on.date().isoformat(),
        expires_on.date().isoformat(),
        received_packs,
        unit_cost_pence
    ))

    eligible_units = list(
        range(1, 5) if chilled else range(5, 13)
    )

    # Exactly half of the batches are split between two units.
    # This produces 450 allocation records.
    number_of_units = 2 if batch_id % 2 == 0 else 1
    selected_units = rng.sample(eligible_units, number_of_units)

    placed_on = manufactured_on + timedelta(
        days=rng.randint(1, 14)
    )

    if number_of_units == 1:
        quantities = [available_packs]
    else:
        first_quantity = max(
            1,
            int(available_packs * rng.uniform(0.3, 0.7))
        )
        quantities = [
            first_quantity,
            available_packs - first_quantity
        ]

    for unit_id, quantity in zip(selected_units, quantities):
        allocations.append((
            batch_id,
            unit_id,
            quantity,
            placed_on.date().isoformat()
        ))


# ============================================================
# 8. Generate temperature readings
# ============================================================

def review_priority(temperature, minimum, maximum):
    """
    Fictional ordinal monitoring rule:
        Low: within range
        Medium: up to 2°C outside range
        High: more than 2°C outside range
    """
    deviation = max(
        minimum - temperature,
        temperature - maximum,
        0
    )

    if deviation == 0:
        return "Low"
    if deviation <= 2:
        return "Medium"
    return "High"


readings = []

for unit in storage_units:
    unit_id = unit[0]
    minimum = unit[5]
    maximum = unit[6]
    midpoint = (minimum + maximum) / 2

    previous_temperature = midpoint + rng.gauss(0, 0.2)

    for sequence in range(1, READINGS_PER_UNIT + 1):
        timestamp = START_DATE + timedelta(hours=sequence - 1)

        daily_cycle = (
            0.35 * math.sin(2 * math.pi * timestamp.hour / 24)
        )

        target_temperature = midpoint + daily_cycle

        # Adjacent readings are correlated.
        previous_temperature = (
            0.8 * previous_temperature
            + 0.2 * target_temperature
            + rng.gauss(0, 0.22 if unit_id <= 4 else 0.45)
        )

        temperature = previous_temperature

        # Retain occasional excursions for monitoring analysis.
        if rng.random() < 0.035:
            if rng.random() < 0.5:
                temperature = minimum - rng.uniform(0.2, 4)
            else:
                temperature = maximum + rng.uniform(0.2, 4)

        temperature = round(temperature, 2)

        humidity = round(
            max(20, min(85, rng.gauss(52, 7))),
            2
        )

        method = rng.choices(
            ["Automatic", "Manual"],
            weights=[97, 3],
            k=1
        )[0]

        priority = review_priority(
            temperature,
            minimum,
            maximum
        )

        if priority != "Low":
            note = "Outside operating range; review required."
        elif method == "Manual":
            note = "Routine manual observation."
        else:
            note = None

        readings.append((
            unit_id,
            sequence,
            timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            temperature,
            humidity,
            method,
            priority,
            note
        ))


# ============================================================
# 9. Deliberately insert missing and duplicate data
# ============================================================

# Choose duplicate sources before selecting missing measurements.
duplicate_source_indices = rng.sample(
    range(len(readings)),
    DUPLICATE_COUNT
)

duplicate_source_set = set(duplicate_source_indices)

# Humidity failures affect automatic readings only.
missing_candidates = [
    index
    for index, record in enumerate(readings)
    if index not in duplicate_source_set
    and record[5] == "Automatic"
]

missing_humidity_indices = rng.sample(
    missing_candidates,
    MISSING_HUMIDITY_COUNT
)

for index in missing_humidity_indices:
    record = list(readings[index])

    # None is stored as SQL NULL.
    record[4] = None

    record[7] = (
        f"{record[7]} Humidity sensor unavailable."
        if record[7]
        else "Humidity sensor unavailable."
    )

    readings[index] = tuple(record)

# Duplicate events represent repeated ingestion.
# Different sequence numbers preserve primary-key uniqueness.
next_sequence = {
    unit[0]: READINGS_PER_UNIT
    for unit in storage_units
}

for source_index in duplicate_source_indices:
    record = list(readings[source_index])
    unit_id = record[0]

    next_sequence[unit_id] += 1
    record[1] = next_sequence[unit_id]

    readings.append(tuple(record))


# ============================================================
# 10. Create and populate the database
# ============================================================

connection = sqlite3.connect(DATABASE_PATH)

try:
    connection.execute("PRAGMA foreign_keys = ON")

    if connection.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0] != 1:
        raise RuntimeError("Foreign-key enforcement is not enabled.")

    connection.executescript(SCHEMA)
    connection.executescript(BUSINESS_RULES)
    create_business_triggers(connection)

    # Insert parents before their dependent child records.
    with connection:
        connection.executemany(
            "INSERT INTO manufacturers VALUES (?, ?, ?, ?)",
            manufacturers
        )

        connection.executemany(
            "INSERT INTO medicines VALUES (?, ?, ?, ?, ?, ?, ?)",
            medicines
        )

        connection.executemany(
            "INSERT INTO storage_units VALUES (?, ?, ?, ?, ?, ?, ?)",
            storage_units
        )

        connection.executemany(
            "INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            batches
        )

        connection.executemany(
            "INSERT INTO batch_storage VALUES (?, ?, ?, ?)",
            allocations
        )

        connection.executemany(
            """
            INSERT INTO temperature_readings
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            readings
        )


    # ========================================================
    # 11. Check table counts, structures and keys
    # ========================================================

    def scalar(sql, parameters=()):
        return connection.execute(sql, parameters).fetchone()[0]


    expected_counts = {
        "manufacturers": 15,
        "medicines": 60,
        "batches": 300,
        "storage_units": 12,
        "batch_storage": 450,
        "temperature_readings": 6060
    }

    print("TABLE COUNTS AND COLUMNS\n")

    composite_keys = {}

    for table, expected_count in expected_counts.items():
        actual_count = scalar(f"SELECT COUNT(*) FROM {table}")

        if actual_count != expected_count:
            raise RuntimeError(f"Unexpected row count in {table}.")

        columns = connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        primary_key = [
            column[1]
            for column in sorted(columns, key=lambda item: item[5])
            if column[5] > 0
        ]

        if not primary_key:
            raise RuntimeError(f"Missing primary key in {table}.")

        if len(primary_key) > 1:
            composite_keys[table] = primary_key

            if any(
                column[5] > 0 and column[3] != 1
                for column in columns
            ):
                raise RuntimeError(
                    f"Nullable composite-key column in {table}."
                )

        print(f"{table}: {actual_count:,} rows")
        print(
            "Columns:",
            ", ".join(
                f"{column[1]} ({column[2]})"
                for column in columns
            )
        )
        print("Primary key:", ", ".join(primary_key))

        foreign_keys = connection.execute(
            f"PRAGMA foreign_key_list({table})"
        ).fetchall()

        for foreign_key in foreign_keys:
            print(
                f"Foreign key: {foreign_key[3]} → "
                f"{foreign_key[2]}.{foreign_key[4]}"
            )

        print()

    if len(composite_keys) < 2:
        raise RuntimeError("At least two composite keys are required.")

    temperature_column_count = len(
        connection.execute(
            "PRAGMA table_info(temperature_readings)"
        ).fetchall()
    )

    if temperature_column_count < 7:
        raise RuntimeError("Seven-column requirement not met.")


    # ========================================================
    # 12. Validate missing and duplicate data
    # ========================================================

    missing_humidity = scalar("""
        SELECT COUNT(*)
        FROM temperature_readings
        WHERE humidity_percent IS NULL
    """)

    missing_emails = scalar("""
        SELECT COUNT(*)
        FROM manufacturers
        WHERE contact_email IS NULL
    """)

    missing_notes = scalar("""
        SELECT COUNT(*)
        FROM temperature_readings
        WHERE note IS NULL
    """)

    duplicate_groups, duplicate_extra_rows = connection.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(copies - 1), 0)
        FROM (
            SELECT COUNT(*) AS copies
            FROM temperature_readings
            GROUP BY storage_unit_id,
                     recorded_at,
                     temperature_c,
                     humidity_percent,
                     recording_method,
                     review_priority,
                     note
            HAVING COUNT(*) > 1
        )
    """).fetchone()

    if missing_humidity != MISSING_HUMIDITY_COUNT:
        raise RuntimeError("Incorrect missing humidity count.")

    if missing_emails != MISSING_EMAIL_COUNT:
        raise RuntimeError("Incorrect missing email count.")

    if (
        duplicate_groups != DUPLICATE_COUNT
        or duplicate_extra_rows != DUPLICATE_COUNT
    ):
        raise RuntimeError("Incorrect duplicate count.")

    total_readings = expected_counts["temperature_readings"]

    print("MISSING AND DUPLICATE DATA\n")

    print(
        f"Missing humidity: {missing_humidity}/{total_readings} "
        f"({missing_humidity / total_readings * 100:.2f}% "
        "of final readings)"
    )

    print(
        "Missing humidity before duplicate insertion: "
        f"{missing_humidity}/6000 "
        f"({missing_humidity / 6000 * 100:.2f}%)"
    )

    print(
        f"Missing manufacturer emails: "
        f"{missing_emails}/{MANUFACTURER_COUNT} "
        f"({missing_emails / MANUFACTURER_COUNT * 100:.2f}%)"
    )

    print(
        f"Optional notes without a comment: "
        f"{missing_notes}/{total_readings} "
        f"({missing_notes / total_readings * 100:.2f}%)"
    )

    print(f"Duplicate event groups: {duplicate_groups}")

    print(
        f"Extra duplicate rows: "
        f"{duplicate_extra_rows}/{total_readings} "
        f"({duplicate_extra_rows / total_readings * 100:.2f}%)"
    )


    # ========================================================
    # 13. Validate relationships, dates and priorities
    # ========================================================

    if scalar(
        "SELECT COUNT(*) FROM allocation_rule_violations"
    ) != 0:
        raise RuntimeError("Inventory business-rule violation found.")

    invalid_priorities = scalar("""
        SELECT COUNT(*)
        FROM temperature_readings r
        JOIN storage_units s
          ON s.storage_unit_id = r.storage_unit_id
        WHERE r.review_priority !=
            CASE
                WHEN r.temperature_c BETWEEN
                     s.operating_min_c AND s.operating_max_c
                THEN 'Low'

                WHEN r.temperature_c >= s.operating_min_c - 2
                 AND r.temperature_c <= s.operating_max_c + 2
                THEN 'Medium'

                ELSE 'High'
            END
    """)

    if invalid_priorities:
        raise RuntimeError("Incorrect review priorities found.")

    invalid_timestamps = scalar("""
        SELECT COUNT(*)
        FROM temperature_readings
        WHERE recorded_at < ?
           OR recorded_at > ?
    """, (
        START_DATE.strftime("%Y-%m-%d %H:%M:%S"),
        END_DATE.strftime("%Y-%m-%d %H:%M:%S")
    ))

    if invalid_timestamps:
        raise RuntimeError("Readings outside the simulation period.")

    invalid_snapshot_dates = scalar("""
        SELECT COUNT(*)
        FROM batches
        WHERE manufactured_on > ?
           OR expires_on <= ?
    """, (
        START_DATE.date().isoformat(),
        END_DATE.date().isoformat()
    ))

    if invalid_snapshot_dates:
        raise RuntimeError("Batch dates are inconsistent with the snapshot.")

    for table, child_table, key in [
        ("medicines", "batches", "medicine_id"),
        ("batches", "batch_storage", "batch_id")
    ]:
        missing_children = scalar(f"""
            SELECT COUNT(*)
            FROM {table} parent
            WHERE NOT EXISTS (
                SELECT 1
                FROM {child_table} child
                WHERE child.{key} = parent.{key}
            )
        """)

        if missing_children:
            raise RuntimeError(
                f"Records in {table} lack expected related records."
            )

    priorities = {
        row[0]
        for row in connection.execute("""
            SELECT DISTINCT review_priority
            FROM temperature_readings
        """)
    }

    if priorities != {"Low", "Medium", "High"}:
        raise RuntimeError("Not all ordinal categories are represented.")


    # ========================================================
    # 14. Test rejection of invalid operations
    # ========================================================

    def expect_rejection(name, sql, parameters=()):
        # All test operations are rolled back.
        connection.execute("SAVEPOINT constraint_test")

        try:
            connection.execute(sql, parameters)

        except sqlite3.IntegrityError:
            print(f"PASS: {name}")

        else:
            raise RuntimeError(
                f"Invalid operation was accepted: {name}"
            )

        finally:
            connection.execute("ROLLBACK TO constraint_test")
            connection.execute("RELEASE constraint_test")


    print("\nCONSTRAINT TESTS\n")

    expect_rejection(
        "Missing required manufacturer name",
        "INSERT INTO manufacturers VALUES (9999, NULL, 'UK', NULL)"
    )

    expect_rejection(
        "Duplicate medicine name",
        """
        INSERT INTO medicines
        SELECT 9999, medicine_name, dosage_form,
               strength_mg_per_unit, units_per_pack,
               storage_min_c, storage_max_c
        FROM medicines
        WHERE medicine_id = 1
        """
    )

    expect_rejection(
        "Invalid foreign key",
        """
        INSERT INTO temperature_readings
        VALUES (
            9999, 1, '2026-01-01 00:00:00',
            5, 50, 'Automatic', 'Low', NULL
        )
        """
    )

    expect_rejection(
        "Duplicate reading composite key",
        """
        INSERT INTO temperature_readings
        SELECT *
        FROM temperature_readings
        WHERE storage_unit_id = 1
          AND reading_sequence = 1
        """
    )

    expect_rejection(
        "Duplicate allocation composite key",
        """
        INSERT INTO batch_storage
        SELECT * FROM batch_storage LIMIT 1
        """
    )

    expect_rejection(
        "Humidity outside the valid range",
        """
        INSERT INTO temperature_readings
        VALUES (
            1, 99999, '2026-01-01 00:00:00',
            5, 120, 'Automatic', 'Low', NULL
        )
        """
    )

    expect_rejection(
        "Invalid ordinal category",
        """
        INSERT INTO temperature_readings
        VALUES (
            1, 99999, '2026-01-01 00:00:00',
            5, 50, 'Automatic', 'Urgent', NULL
        )
        """
    )

    expect_rejection(
        "Negative stock quantity",
        """
        UPDATE batch_storage
        SET quantity_available = -1
        WHERE batch_id = 1
        """
    )

    expect_rejection(
        "Stock exceeding received quantity",
        """
        UPDATE batch_storage
        SET quantity_available = 999999
        WHERE batch_id = 1
        """
    )

    expect_rejection(
        "Storage capacity below allocated stock",
        """
        UPDATE storage_units
        SET capacity_packs = 1
        WHERE storage_unit_id = 1
        """
    )

    expect_rejection(
        "Expiry before manufacturing",
        """
        UPDATE batches
        SET expires_on = '2000-01-01'
        WHERE batch_id = 1
        """
    )

    expect_rejection(
        "Invalid calendar date",
        """
        UPDATE batches
        SET manufactured_on = '2025-02-30'
        WHERE batch_id = 1
        """
    )

    incompatible_pair = connection.execute("""
        SELECT b.batch_id, s.storage_unit_id
        FROM batches b
        JOIN medicines m ON m.medicine_id = b.medicine_id
        CROSS JOIN storage_units s
        WHERE (
            s.operating_min_c < m.storage_min_c
            OR s.operating_max_c > m.storage_max_c
        )
        AND NOT EXISTS (
            SELECT 1
            FROM batch_storage a
            WHERE a.batch_id = b.batch_id
              AND a.storage_unit_id = s.storage_unit_id
        )
        LIMIT 1
    """).fetchone()

    expect_rejection(
        "Incompatible batch storage",
        """
        INSERT INTO batch_storage
        VALUES (?, ?, 1, '2026-01-01')
        """,
        incompatible_pair
    )


    # ========================================================
    # 15. Final integrity checks
    # ========================================================

    integrity_results = [
        row[0]
        for row in connection.execute("PRAGMA integrity_check")
    ]

    foreign_key_errors = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if integrity_results != ["ok"]:
        raise RuntimeError(f"Integrity check failed: {integrity_results}")

    if foreign_key_errors:
        raise RuntimeError(
            f"Foreign-key errors: {foreign_key_errors}"
        )

    # Ensure rolled-back tests did not change the row counts.
    for table, expected_count in expected_counts.items():
        if scalar(f"SELECT COUNT(*) FROM {table}") != expected_count:
            raise RuntimeError(
                f"Constraint tests changed the records in {table}."
            )

    print("\nFINAL VALIDATION\n")
    print("Integrity check: ok")
    print("Foreign-key violations: 0")
    print("Inventory business-rule violations: 0")
    print("Temperature table: 8 columns and 6,060 rows")
    print("Composite primary keys: 2")
    print("All four measurement types represented")
    print("Deliberate missing and duplicate data retained")


    # ========================================================
    # 16. Example SQL retrieval
    # ========================================================

    print("\nTOP FIVE MEDICINES BY AVAILABLE STOCK\n")

    stock_results = connection.execute("""
        SELECT m.medicine_name,
               m.dosage_form,
               SUM(a.quantity_available) AS available_packs
        FROM medicines m
        JOIN batches b ON b.medicine_id = m.medicine_id
        JOIN batch_storage a ON a.batch_id = b.batch_id
        GROUP BY m.medicine_id, m.medicine_name, m.dosage_form
        ORDER BY available_packs DESC
        LIMIT 5
    """).fetchall()

    for name, form, quantity in stock_results:
        print(f"{name} | {form} | {quantity:,} packs")

    print("\nMONITORING PRIORITIES\n")

    priority_results = connection.execute("""
        SELECT review_priority, COUNT(*)
        FROM temperature_readings
        GROUP BY review_priority
        ORDER BY CASE review_priority
            WHEN 'Low' THEN 1
            WHEN 'Medium' THEN 2
            WHEN 'High' THEN 3
        END
    """).fetchall()

    for priority, count in priority_results:
        print(f"{priority}: {count:,}")

    connection.commit()

finally:
    connection.close()


# ============================================================
# ============================================================

with sqlite3.connect(DATABASE_PATH) as saved_connection:
    saved_connection.execute("PRAGMA foreign_keys = ON")

    saved_count = saved_connection.execute("""
        SELECT COUNT(*) FROM temperature_readings
    """).fetchone()[0]

    saved_integrity = saved_connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if saved_count != 6060 or saved_integrity != "ok":
        raise RuntimeError("Saved database verification failed.")

print("\nDatabase saved and reopened successfully.")
print("Downloading medicine_monitoring.db ...")

files.download(str(DATABASE_PATH))