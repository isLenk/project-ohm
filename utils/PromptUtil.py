from collections import deque
class PromptUtil:
    token_size: int

    def __init__(self, token_size: int):
        self.token_size = token_size

    def fetch_prompt_size(self):
        """Query the token size of the prompt"""
        return 32
    
    def __call__(self, vals: deque):
        """Generate a prompt from a deque of values"""
        prompt = ""

        while vals:
            val = vals.popleft()

            # If the prompt cannot fit the value, return the value
            if len(prompt) + len(val) + 1 > self.token_size:
                vals.appendleft(val)
                break

            prompt += val + " "

        return prompt.strip()
        



if __name__ == '__main__':
    vals = [
        "hello there",
        "general kenobi",
        "you are a bold one",
        "i am the senate",
        "not yet",
        "it's treason then",
        "i have the high ground",
        "you underestimate my power",
        "don't try it",
        "i hate you",
    ]

    vals = deque(vals)
    prompt_util = PromptUtil(55)
    print("Prior:", len(vals))
    prompt = prompt_util(vals)
    print("Post:", len(vals))
    print("Propmt length:", len(prompt))
    print(prompt)