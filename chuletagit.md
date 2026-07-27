# Chuleta de Git

Para consultar sin conexión. Ordenada por lo que realmente vas a usar, no por
lo que sale en los tutoriales.

---

## Antes del primer commit (una sola vez)

```bash
# Identidad: aparece en cada commit que hagas
git config --global user.name "Tu Nombre"
git config --global user.email "tu@email.com"

# Nombre de la rama principal por defecto (moderno)
git config --global init.defaultBranch main
```

Comprueba que `.env` está ignorado **antes** del primer commit. Si tu API key
entra en el historial, borrarla luego del último commit **no la quita del
historial**:

```bash
cat .gitignore                    # debe contener .env, data/, .venv/, __pycache__/
git status --short                # .env NO debe aparecer aquí
```

---

## El ciclo diario (el 95% de tu uso)

```bash
git status          # ¿en qué estado estoy? Úsalo constantemente.
git diff            # ¿qué he cambiado exactamente, línea a línea?
git add -A          # preparar TODOS los cambios
git commit -m "..." # guardar el punto de restauración
git log --oneline   # ver el historial compacto
```

**El modelo mental:** hay tres zonas.

```
   working tree          staging area          repositorio
  (tus archivos)   →     (git add)      →     (git commit)
```

`git add` dice "esto quiero que entre en el próximo commit".
`git commit` sella el paquete y lo guarda para siempre.

---

## Mensajes de commit

Un buen mensaje explica **por qué**, no qué archivos tocaste (eso ya lo dice el
diff). En imperativo, primera línea corta:

```
✅ Añade caché de Overpass para evitar el rate limit de Nominatim
✅ Corrige timeout en cascada: espejos escalonados en vez de en serie
✅ Fase 2: recomendaciones con Claude + contexto meteorológico

❌ cambios
❌ arreglos varios
❌ update location_context.py
```

Regla práctica: si no puedes resumir el commit en una línea, probablemente son
dos commits.

---

## Deshacer cosas (lo que salva vidas)

```bash
# Descartar cambios de un archivo que NO has commiteado (¡se pierden!)
git restore app/modules/weather_context.py

# Descartar TODOS los cambios sin commitear (¡cuidado!)
git restore .

# Quitar algo del staging pero conservar el cambio en el archivo
git restore --staged app/config.py

# Corregir el mensaje del último commit
git commit --amend -m "mensaje corregido"

# Volver a como estaba en un commit anterior, SIN perder el historial
git revert <hash>

# Ver cómo era un archivo en un commit anterior
git show <hash>:app/modules/storage.py
```

`git log --oneline` te da los hashes (los códigos cortos tipo `a3f9c21`).

> **La regla de oro:** commitea a menudo. Un commit es gratis. Volver atrás sin
> commits no es posible.

---

## Cuando quieras subirlo a GitHub

```bash
# 1. Crea el repo vacío en github.com (sin README, sin .gitignore)
# 2. Conéctalo:
git remote add origin git@github.com:TU_USUARIO/roadtrip.git
git branch -M main
git push -u origin main

# Y a partir de ahí, después de cada commit:
git push
```

Para que `git push` no te pida la contraseña cada vez, genera una clave SSH:

```bash
ssh-keygen -t ed25519 -C "tu@email.com"
cat ~/.ssh/id_ed25519.pub          # copia esto en GitHub → Settings → SSH keys
```

**En el móvil durante el viaje:** la app **Working Copy** (iOS) es un cliente
Git completo. Te sirve para revisar diffs, leer código y hacer commits pequeños
sin abrir el portátil.

---

## Lo que NO necesitas aprender todavía

Ramas, `merge`, `rebase`, `cherry-pick`, `stash`, pull requests. Todo eso
existe para **coordinar varias personas** o para experimentos paralelos. Estás
tú solo en un proyecto lineal: `main` y commits frecuentes es el flujo correcto,
y añadir ramas ahora solo te confundiría.

Cuando te haga falta, lo notarás: será el día que quieras probar algo grande sin
romper lo que funciona. Entonces aprenderás `git switch -c experimento` y ya.

---

## Ficheros clave

| Archivo | Qué es |
|---------|--------|
| `.gitignore` | Lista de lo que Git debe ignorar. **`.env` va aquí, siempre.** |
| `.git/` | El historial completo. No lo toques a mano nunca. |

---

## Si algo va mal y no entiendes qué

```bash
git status          # el 80% de las veces te lo explica él solo
git log --oneline -10
```

Y si sigues perdido: pégale la salida de `git status` a Claude Code y pregúntale
qué pasó. Está en el contexto de tu proyecto y te lo puede explicar sobre tu
caso concreto, que es como se aprende esto de verdad.