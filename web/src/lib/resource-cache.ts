interface ResourceCacheOptions {
    maxEntries: number;
    ttlMs: number;
}

interface CacheEntry<V> {
    value: V;
    expiresAt: number;
    tags: ReadonlySet<string>;
}

/** Small bounded TTL/LRU cache with entity tags for event-driven invalidation. */
export class ResourceCache<K, V> {
    private readonly entries = new Map<K, CacheEntry<V>>();

    constructor(private readonly options: ResourceCacheOptions) {
        if (options.maxEntries < 1) throw new Error("maxEntries must be positive");
        if (options.ttlMs < 1) throw new Error("ttlMs must be positive");
    }

    get size(): number {
        this.pruneExpired();
        return this.entries.size;
    }

    has(key: K): boolean {
        return this.get(key) !== undefined;
    }

    get(key: K): V | undefined {
        const entry = this.entries.get(key);
        if (!entry) return undefined;
        if (entry.expiresAt <= Date.now()) {
            this.entries.delete(key);
            return undefined;
        }

        // Reinsert on access so Map insertion order is the LRU order.
        this.entries.delete(key);
        this.entries.set(key, entry);
        return entry.value;
    }

    set(key: K, value: V, { tags = [] }: { tags?: Iterable<string> } = {}): this {
        this.entries.delete(key);
        this.entries.set(key, {
            value,
            expiresAt: Date.now() + this.options.ttlMs,
            tags: new Set(tags),
        });
        this.enforceCapacity();
        return this;
    }

    delete(key: K): boolean {
        return this.entries.delete(key);
    }

    clear(): void {
        this.entries.clear();
    }

    invalidateTag(tag: string): K[] {
        const invalidated: K[] = [];
        for (const [key, entry] of this.entries) {
            if (!entry.tags.has(tag)) continue;
            this.entries.delete(key);
            invalidated.push(key);
        }
        return invalidated;
    }

    private pruneExpired(): void {
        const now = Date.now();
        for (const [key, entry] of this.entries) {
            if (entry.expiresAt <= now) this.entries.delete(key);
        }
    }

    private enforceCapacity(): void {
        while (this.entries.size > this.options.maxEntries) {
            const oldest = this.entries.keys().next();
            if (oldest.done) return;
            this.entries.delete(oldest.value);
        }
    }
}
