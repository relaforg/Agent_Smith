# Architecture de la sandbox — notes de conception

Notes issues de la phase de brainstorming. Elles documentent **les décisions et
leurs justifications**, pas l'implémentation (voir `src/agent_core/sandbox/`).

Références au sujet (`en.subject.pdf`, v1.1) entre parenthèses.

---

## 1. Le point de départ : peut-on exécuter du code via un PID ?

Non. Un PID est un identifiant, pas un canal de communication. Depuis un PID
seul, le noyau n'autorise que :

- l'envoi de **signaux** (`os.kill`) ;
- l'**attente** de la terminaison (`os.waitpid`) ;
- la **lecture d'état** via `/proc/<pid>/`.

Injecter du code dans un processus déjà lancé demande `ptrace` (ce que fait
`gdb`) : privilèges élevés, spécifique à Linux, et philosophiquement inverse de
ce qu'on veut. Une sandbox ne s'introduit pas par effraction dans son enfant :
l'enfant **coopère**.

**Conséquence :** le canal doit être créé **avant** le `fork`. Les descripteurs
d'un `os.pipe()` créé avant le fork sont hérités par l'enfant — c'est le lien.
Après le fork, il est trop tard.

---

## 2. Le namespace est persistant entre les appels à `execute()`

Question ouverte au départ : le sujet le spécifie pour le REPL mais pas
explicitement pour la boucle d'agent. Il l'est en fait, ailleurs.

- **REPL** (V.2.1, p. 13) : « execute each entry inside **the** sandbox
  namespace ». Article défini + singulier : un seul namespace pour toutes les
  entrées.
- **Agent** (III.1, p. 7) : le *code-based tool calling* est justifié par
  « **Persistent variables between steps** ». Le schéma de la même page montre
  une unique session `>>>` où `result = search_code(...)` d'un bloc est réutilisé
  par le `print(result)` du bloc suivant.

De plus, `SandboxProtocol` (`src/agent_core/models.py`) n'expose qu'un seul
`execute(code) -> ExecutionResult`, sans mode ni paramètre `reset` : le REPL et
la boucle d'agent consomment la même sandbox par la même interface. Un
comportement différent entre les deux serait une divergence que rien ne demande.

Sans persistance, l'exemple du sujet est cassé et l'argument « plus expressif que
le JSON tool calling » s'effondre. **La persistance est la valeur ajoutée du code
agent.**

**Conséquence :** namespace persistant ⇒ **processus enfant persistant**. Pas de
`fork` par appel à `execute()`.

---

## 3. Architecture retenue

Deux processus, reliés par deux pipes créés avant le `fork`.

```
        PARENT (agent / REPL)                    ENFANT (exécution)
        ─────────────────────                    ──────────────────
        boucle d'agent                           namespace persistant
        client MCP                               limites RLIMIT / alarm
              │                                        │
              │   pipe  parent → enfant  (code)        │
              ├────────────────────────────────────────>
              │                                        │
              │   pipe  enfant → parent  (résultats,   │
              <────────────────────────────────────────┤
                          demandes d'outils MCP)
```

Le parent possède une **instance unique** de `Sandbox` — pas un singleton au sens
strict (globale + `__new__` détourné). La `Sandbox` prend une `SandboxConfig` en
paramètre : la globaliser la rendrait non testable et brouillerait le cycle de
vie (qui appelle `close()` ?). « Un seul objet existe » et « le langage
m'empêche d'en créer deux » sont deux propriétés différentes ; seule la première
est souhaitée. L'instance est créée et possédée par la boucle d'agent (ou par le
REPL), et passée explicitement.

### Structure obligatoire après le fork

```python
self.pid = os.fork()
if self.pid == 0:
    # ENFANT : ne revient jamais dans le code de l'agent
    try:
        self._serve()          # boucle lire / exécuter / répondre
    finally:
        os._exit(0)
# PARENT : continue normalement
```

Deux pièges corrigés par rapport à la première version du code :

- `os.fork()` **ne renvoie jamais de valeur négative** en Python : il lève
  `OSError`. Le `if pid < 0` est un idiome C sans équivalent ici.
- Sans branchement sur `pid == 0`, **deux** processus sortent de `__init__` et
  exécutent tout le programme d'agent (appels LLM, écriture de `solution.json`…).

`os._exit` et non `sys.exit` : `sys.exit` lève une exception rattrapable et
déclenche les handlers `atexit` ainsi que le flush des buffers hérités du parent
— la sortie du parent serait réécrite en double.

### L'attente du parent ne nécessite pas de thread

Un thread sert à faire deux choses en même temps. Pendant l'exécution, le parent
n'a rien d'autre à faire : il ne peut ni appeler le LLM (il attend justement
l'observation), ni rendre la main à l'utilisateur du REPL. Un `read` bloquant sur
le pipe est le bon outil ; le parent dort dans le noyau.

Le seul besoin réel — « et si l'enfant ne répond jamais ? » — se traite sans
thread :

```python
ready, _, _ = select.select([self.read_fd], [], [], deadline)
if not ready:
    os.kill(self.pid, signal.SIGKILL)   # puis respawn
```

`select` = un `read` bloquant avec réveil. Un watchdog thread ferait la même
chose, en plus fragile.

### Threads + fork : ordre imposé dans `__init__`

`fork()` ne duplique **que le thread appelant**. Les autres threads n'existent
pas dans l'enfant, mais **les verrous qu'ils tenaient au moment du fork restent
verrouillés définitivement**. Si un thread de la bibliothèque MCP tenait le lock
du module `logging` à cet instant, l'enfant se fige au premier `print` — bug
intermittent et très difficile à diagnostiquer.

D'où l'ordre non négociable :

1. `os.fork()` — l'enfant naît d'un processus mono-thread, propre ;
2. seulement ensuite : connexion du client MCP, démarrage de l'asyncio, etc.

---

## 4. Timeout et mémoire : deux niveaux

Tension à résoudre : le namespace doit survivre, mais le code qui dépasse
`max_execution_time_seconds` doit être arrêté. Si le seul moyen est un `SIGKILL`
sur l'enfant, chaque timeout détruit l'état.

**Niveau 1 — dans l'enfant (cas nominal, l'état survit).**
`signal.alarm(timeout)` avec un handler qui lève une exception : elle remonte
dans la boucle `_serve`, est rattrapée, et produit un
`ExecutionResult(timed_out=True)`. Le namespace est intact. Idem pour la
mémoire : `resource.setrlimit(RLIMIT_AS, ...)` fait lever un `MemoryError`
rattrapable.

**Niveau 2 — dans le parent (filet de sécurité, l'état est perdu).**
`SIGALRM` ne peut interrompre l'interpréteur qu'entre deux bytecodes : un
`re.match` en backtracking catastrophique ou une boucle en C ne rendra jamais la
main. Le parent applique donc une deadline plus large via `select` ; en cas
d'expiration, `SIGKILL` + respawn.

Les limites (`setrlimit`, `chdir`, restriction réseau…) sont posées **dans
l'enfant, après le fork**, pour ne pas contaminer le parent.

### Retour d'information au LLM

Le sujet l'exige explicitement (V.1, p. 11, encadré rouge) : la sandbox doit
signaler notamment « Execution hit the timeout and output is partial ». À ajouter
pour notre architecture : **en cas de respawn (niveau 2), il faut prévenir le LLM
que ses variables ont disparu**, sinon il écrira du code référençant un état
inexistant et brûlera des itérations.

### Propagation des exceptions

`KeyboardInterrupt` et `SystemExit` ne doivent pas être silencieusement avalés
(V.2.2, p. 15) : ils doivent atteindre la boucle d'agent. `except Exception` est
correct — il n'attrape ni l'un ni l'autre, qui héritent de `BaseException`. C'est
`except:` nu ou `except BaseException:` qui serait la faute.

---

## 5. Placement du client MCP : dans le parent

Le sujet ne tranche pas au niveau des processus. Le schéma p. 8 dessine le client
MCP dans la boîte « Sandbox », mais c'est un schéma **logique** (la sandbox
possède le client — « The sandbox wraps the MCP client », p. 17). En revanche il
impose deux propriétés :

- « MCP tool actions happen **outside** the sandbox » (p. 17) ;
- « Actions performed by the MCP server […] are **not subject to the sandbox
  timeout** » (p. 17).

**Option écartée — client dans l'enfant.** Plus simple (le wrapper appelle
directement le serveur), mais l'enfant est précisément le processus où l'accès
réseau est coupé. Il faudrait distinguer « réseau de la machinerie » et « réseau
du code LLM », ce qui est pénible avec un blocage global.

**Option retenue — client dans le parent.** Le wrapper injecté dans le namespace
de l'enfant est un **stub** : il sérialise `(nom_outil, args)` vers le pipe
retour et attend la réponse. Le parent exécute l'appel MCP et renvoie le
résultat. Les actions MCP se déroulent bien hors de la sandbox, dans un domaine
de sécurité distinct.

### Implication sur le protocole — à prévoir dès la sandbox simple

L'attente du parent n'est plus « une requête → une réponse » mais une **petite
boucle de messages** :

```
envoyer le code
boucle:
    lire un message de l'enfant
    si type == "tool_call" -> exécuter l'outil MCP, renvoyer le résultat, continuer
    si type == "result"    -> sortir
```

C'est du RPC bidirectionnel sur les mêmes pipes. Ce n'est pas plus difficile à
coder, mais un protocole conçu sans champ `type` devrait être entièrement repris.
**Prévoir `{"type": ...}` dans les messages JSON dès la version sans MCP** ne
coûte rien maintenant.

Bénéfice : la règle « les actions MCP échappent au timeout » devient gratuite. Le
stub fait `signal.alarm(0)` avant d'envoyer, puis réarme le reliquat au retour —
l'horloge s'arrête pendant l'appel outil.

---

## 6. Détails de protocole

- **Cadrage des messages** : un pipe n'a aucune notion de message. Utiliser une
  ligne JSON par message (`json.dumps(...) + "\n"`) ou un préfixe de longueur sur
  4 octets. Ne jamais lire « jusqu'à la fin ».
- **Fermeture des extrémités inutilisées** : chaque côté ferme les fd du pipe
  qu'il n'utilise pas, sinon aucun `read` ne verra jamais d'EOF à la mort de
  l'autre.
- **Capture de `stdout`** : l'enfant hérite du `stdout` du parent. Sans
  `contextlib.redirect_stdout` autour de l'`exec`, les `print()` du code LLM
  s'afficheront dans le terminal au lieu d'alimenter `ExecutionResult.stdout`, et
  l'observation renvoyée au LLM sera vide.

---

## Ordre d'implémentation

1. Sandbox simple : fork, deux pipes, boucle `_serve`, protocole JSON **avec
   champ `type`**, capture de stdout/stderr.
2. Limites : `setrlimit`, `signal.alarm` côté enfant ; deadline `select` +
   respawn côté parent ; messages de feedback au LLM.
3. Restrictions : imports (allowlist), chemins (`allowed_directories`), réseau,
   builtins restreints.
4. `final_answer` injecté dans le namespace.
5. Client MCP côté parent + stubs côté enfant ; génération du manuel depuis les
   schémas d'outils découverts.
