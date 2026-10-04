import sqlite3
import os
import shutil

conn = sqlite3.connect('chroma/chroma.sqlite3')
conn.row_factory = sqlite3.Row

# Find orphaned stage and backup collections
orphaned = conn.execute(
    "SELECT id, name FROM collections WHERE name LIKE 'stage_%' OR name LIKE 'backup_%' OR name = 'test_col'"
).fetchall()

print(f"Found orphaned collections to clean: {len(orphaned)}")
for col in orphaned:
    col_id = col['id']
    col_name = col['name']
    print(f"Cleaning {col_name} ({col_id})...")
    
    # Find segments for this collection
    segs = conn.execute('SELECT id FROM segments WHERE collection = ?', (col_id,)).fetchall()
    for s in segs:
        seg_id = s['id']
        seg_dir = os.path.join('chroma', seg_id)
        if os.path.exists(seg_dir):
            try:
                shutil.rmtree(seg_dir)
                print(f"  Removed segment dir: {seg_dir}")
            except Exception as e:
                print(f"  Could not remove {seg_dir}: {e}")
        conn.execute('DELETE FROM segment_metadata WHERE segment_id = ?', (seg_id,))
        conn.execute('DELETE FROM segments WHERE id = ?', (seg_id,))
    
    conn.execute('DELETE FROM collection_metadata WHERE collection_id = ?', (col_id,))
    conn.execute('DELETE FROM collections WHERE id = ?', (col_id,))

conn.commit()

# Also clean any orphaned directories not matching any active segment
active_segs = {r['id'] for r in conn.execute('SELECT id FROM segments').fetchall()}
for item in os.listdir('chroma'):
    p = os.path.join('chroma', item)
    if os.path.isdir(p) and item not in active_segs:
        try:
            shutil.rmtree(p)
            print(f"Removed orphaned directory: {p}")
        except Exception as e:
            print(f"Could not remove {p}: {e}")

conn.close()
print("Orphaned staging collections cleaned successfully!")
