// One bulk getter per property; never per-note calls or `whose` filters.
function run() {
  const accounts = [];
  for (const account of Application("Notes").accounts()) {
    const folders = { ids: [], names: [], parents: [], notes: [] };
    if (account.folders.length > 0) {
      folders.ids = account.folders.id();
      folders.names = account.folders.name();
      folders.parents = account.folders.container.id();
      folders.notes = account.folders.notes.id();
    }
    const notes = { ids: [], names: [], created: [], modified: [], locked: [] };
    if (account.notes.length > 0) {
      notes.ids = account.notes.id();
      notes.names = account.notes.name();
      notes.created = account.notes.creationDate();
      notes.modified = account.notes.modificationDate();
      notes.locked = account.notes.passwordProtected();
    }
    accounts.push({ id: account.id(), name: account.name(), folders, notes });
  }
  return JSON.stringify({ accounts });
}
