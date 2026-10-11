"""Small transactional fake: commits only after the callback succeeds."""

import copy
import threading


class Snapshot:
    def __init__(self, reference, value):
        self.reference = reference
        self.id = reference.id
        self.value = copy.deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return copy.deepcopy(self.value)


class Reference:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.id = path.split("/")[-1]

    def get(self, transaction=None):
        return Snapshot(self, self.db.documents.get(self.path))

    def set(self, value, merge=False):
        current = self.db.documents.get(self.path, {}) if merge else {}
        self.db.documents[self.path] = copy.deepcopy({**current, **value})

    def create(self, value):
        if self.path in self.db.documents:
            raise ValueError("Already exists")
        self.set(value)

    def update(self, value):
        if self.path not in self.db.documents:
            raise KeyError(self.path)
        self.set(value, merge=True)

    def delete(self):
        self.db.documents.pop(self.path, None)


class Collection:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, name):
        return Reference(self.db, f"{self.path}/{name}")

    def stream(self):
        return [
            Snapshot(Reference(self.db, key), value)
            for key, value in self.db.documents.items()
            if key.rsplit("/", 1)[0] == self.path
        ]

    def where(self, *, filter):
        return Query(self.stream()).where(filter=filter)


class Query:
    def __init__(self, snapshots, *, orders=(), cursor=None, count=None):
        self.snapshots = snapshots
        self.orders, self.cursor, self.count = orders, cursor, count

    def _copy(self, **changes):
        return Query(
            changes.get("snapshots", self.snapshots),
            orders=changes.get("orders", self.orders),
            cursor=changes.get("cursor", self.cursor),
            count=changes.get("count", self.count),
        )

    def where(self, *, filter):
        assert filter.op_string in {"==", "in"}
        values = filter.value if filter.op_string == "in" else [filter.value]
        return self._copy(
            snapshots=[
                snapshot
                for snapshot in self.snapshots
                if snapshot.to_dict().get(filter.field_path) in values
            ]
        )

    def order_by(self, name, direction="ASCENDING"):
        return self._copy(orders=(*self.orders, (name, direction)))

    def start_after(self, cursor):
        return self._copy(cursor=cursor)

    def limit(self, count):
        return self._copy(count=count)

    def stream(self):
        def field(snapshot, name):
            return snapshot.id if name == "__name__" else snapshot.to_dict()[name]

        snapshots = list(self.snapshots)
        for name, direction in reversed(self.orders):
            snapshots.sort(
                key=lambda snapshot, name=name: field(snapshot, name),
                reverse=direction == "DESCENDING",
            )
        if self.cursor:

            def after(snapshot):
                for name, direction in self.orders:
                    value, bound = field(snapshot, name), self.cursor[name]
                    if name == "__name__":
                        bound = bound.id
                    if value != bound:
                        return value < bound if direction == "DESCENDING" else value > bound
                return False

            snapshots = [snapshot for snapshot in snapshots if after(snapshot)]
        return snapshots[: self.count] if self.count is not None else snapshots


class Transaction:
    def __init__(self):
        self.writes = []

    def set(self, ref, value, merge=False):
        self.writes.append(lambda: ref.set(value, merge=merge))

    def update(self, ref, value):
        self.writes.append(lambda: ref.update(value))

    def delete(self, ref):
        self.writes.append(ref.delete)

    def commit(self):
        for operation in self.writes:
            operation()


class FakeDB:
    def __init__(self):
        self.documents = {}
        self.lock = threading.RLock()

    def collection(self, name):
        return Collection(self, name)

    def get_all(self, references):
        return [ref.get() for ref in reversed(references)]

    def atomic(self, callback):
        with self.lock:
            transaction = Transaction()
            value = callback(transaction)
            transaction.commit()
            return value

    def transaction(self):
        return Transaction()

    def batch(self):
        return Transaction()


class FakeStore:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, name, data, content_type="application/octet-stream"):
        if name in self.objects:
            raise ValueError("Immutable object exists")
        self.objects[name] = data
        return {"bucket": "test-private", "name": name, "generation": "1"}

    def read_bytes(self, ref):
        return self.objects[ref["name"]]

    def lookup_reference(self, name):
        if name not in self.objects:
            return None
        return {"bucket": "test-private", "name": name, "generation": "1"}

    def put_bytes_idempotent(self, name, data, content_type="application/octet-stream"):
        existing = self.lookup_reference(name)
        if existing:
            if self.read_bytes(existing) != data:
                raise ValueError("Existing immutable object contains different bytes")
            return existing
        return self.put_bytes(name, data, content_type)

    def delete(self, ref):
        self.objects.pop(ref["name"], None)
