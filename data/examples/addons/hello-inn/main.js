// Hello, Inn: the example folder addon (docs/ADDONS.md). It shows a small sign with the helper and quest counts and greets you once.
export default function ({ onState, panel, toast, addon }) {
  const body = panel('Hello, Inn');
  body.classList.add('hello-inn');
  const line = document.createElement('div');
  const quests = document.createElement('div');
  quests.className = 'hello-inn-quests';
  body.append(line, quests);
  onState((s) => {
    const n = (s.agents || []).length;
    line.textContent = n === 1 ? '1 helper in the inn' : n + ' helpers in the inn';
    quests.textContent = s.counts.done + ' of ' + s.counts.total + ' quests done';
  });
  toast('Hello from the inn! (' + addon.name + ' ' + addon.version + ')');
}
