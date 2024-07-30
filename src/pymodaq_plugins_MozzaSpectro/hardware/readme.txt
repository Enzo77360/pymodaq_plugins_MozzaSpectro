J’ai joint une archive comprenant un fichier whl avec libmozza.dll et les fichiers py du wrapper :

Explication de Raman : « Pour utiliser libmozza.dll dans Fastlite Spectro j'ai fait 2 couches du code Python. La première couche est effectivement le wrapper manuel, qui peut servir de la doc pour la DLL, la deuxième couche est l'adaptation à l'API du Spectro.

Je trouve que le nombre de fonctions dans la DLL est assez petit et elles ne changent pas souvent. Ça justifier le wrapper manuel, beaucoup plus claire qu'un wrapper automatique. En plus ça permet d'avoir une seule version de la librairie Python, indépendamment de la version Python

Le code du wrapper libmozza dans le wheel est mozza.py

Dans l’archive il y a aussi le code de la couche mozza/Spectro avec des exemples d'utilisation de mozza.py : il s’appelle Mozza.py. »

J’espère que les exemples seront assez clairs. Il faut s’appuyer sur le manuel du Mozza et la pratique du soft pour comprendre les fonctions.
