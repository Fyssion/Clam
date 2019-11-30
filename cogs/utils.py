import random
from nltk.corpus import wordnet
import asyncio


async def start():
    wordnet.synsets("test")
    

async def thesaurize(msg):
    isInput = False
    IncorrectMsg = ""

    args = msg.split(" ")

    if len(args) < 2:
        minReplace = 0
    else:
        minReplace = 1

    # Replace random # of items in the list with random item from GList

    newMsg = args
    toBeReplaced = []
    for i in range(random.randrange(minReplace, len(args))):

        isVaild = False
        while isVaild == False:

            
            num = random.randrange(0, len(args))
            
            
            if num in toBeReplaced:
                pass
            elif len(args[num]) < 4:
                pass
            else:
                toBeReplaced.append(num)
                isVaild = True
                newWord = (wordnet.synsets(args[num]))#[0].lemmas()[0].name()
                if len(newWord) <= 0:
                    pass
                else:
                    newWord = newWord[0].lemmas()[0].name()

                    newMsg[num] = newWord

                break


    return " ".join(args)
