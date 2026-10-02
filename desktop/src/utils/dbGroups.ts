/**
 * Utility functions and types for managing Custom Database Groups.
 * Allows users to group 2 or more connected SQL/POS databases (e.g. inventory + sales)
 * under a single custom user-defined name (e.g. "Asaan POS").
 */

export interface CustomDbGroup {
    id: string;
    name: string;
    dbNames: string[];
    domain: string;
    createdAt: string;
    description?: string;
}

const STORAGE_KEY_PREFIX = 'llm_konnect_custom_db_groups';

export function getCustomDbGroups(domain?: string): CustomDbGroup[] {
    try {
        if (!domain) {
            // If domain not specified, collect all groups from all domain keys + global key
            const results: CustomDbGroup[] = [];
            const seenIds = new Set<string>();
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (k && k.startsWith(STORAGE_KEY_PREFIX)) {
                    try {
                        const parsed = JSON.parse(localStorage.getItem(k) || '[]');
                        if (Array.isArray(parsed)) {
                            parsed.forEach((g: CustomDbGroup) => {
                                if (g && g.id && !seenIds.has(g.id)) {
                                    seenIds.add(g.id);
                                    results.push(g);
                                }
                            });
                        }
                    } catch {}
                }
            }
            return results;
        }

        const key = `${STORAGE_KEY_PREFIX}_${domain.toLowerCase().trim()}`;
        const raw = localStorage.getItem(key);
        if (!raw) {
            // Also check global fallback key if domain-specific isn't set yet
            const globalRaw = localStorage.getItem(STORAGE_KEY_PREFIX);
            if (globalRaw) {
                const parsed: CustomDbGroup[] = JSON.parse(globalRaw);
                return parsed.filter(g => !g.domain || g.domain.toLowerCase() === domain.toLowerCase().trim());
            }
            return [];
        }
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
        console.error('Failed to load custom db groups from storage:', err);
        return [];
    }
}

export function saveCustomDbGroup(
    group: Omit<CustomDbGroup, 'id' | 'createdAt'> & { id?: string }
): CustomDbGroup {
    const domain = (group.domain || 'pharmacy').toLowerCase().trim();
    const key = `${STORAGE_KEY_PREFIX}_${domain}`;
    const current = getCustomDbGroups(domain);

    const now = new Date().toISOString();
    const existingIndex = group.id ? current.findIndex(g => g.id === group.id) : -1;

    let savedGroup: CustomDbGroup;

    if (existingIndex >= 0) {
        savedGroup = {
            ...current[existingIndex],
            name: group.name.trim(),
            dbNames: Array.from(new Set(group.dbNames.map(d => d.trim()))),
            domain,
            description: group.description
        };
        current[existingIndex] = savedGroup;
    } else {
        savedGroup = {
            id: group.id || `group-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
            name: group.name.trim(),
            dbNames: Array.from(new Set(group.dbNames.map(d => d.trim()))),
            domain,
            createdAt: now,
            description: group.description
        };
        current.push(savedGroup);
    }

    try {
        localStorage.setItem(key, JSON.stringify(current));
        // Dispatch custom event for real-time reactivity across components
        window.dispatchEvent(new CustomEvent('custom-db-groups-changed', { detail: { domain, groups: current } }));
    } catch (err) {
        console.error('Failed to save custom db group:', err);
    }

    return savedGroup;
}

export function deleteCustomDbGroup(groupId: string, domain?: string): void {
    const dom = (domain || 'pharmacy').toLowerCase().trim();
    const key = `${STORAGE_KEY_PREFIX}_${dom}`;
    const current = getCustomDbGroups(dom);
    const updated = current.filter(g => g.id !== groupId);

    try {
        localStorage.setItem(key, JSON.stringify(updated));
        window.dispatchEvent(new CustomEvent('custom-db-groups-changed', { detail: { domain: dom, groups: updated } }));
    } catch (err) {
        console.error('Failed to delete custom db group:', err);
    }
}
