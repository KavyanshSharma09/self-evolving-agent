import sys
from agent_loop import run_agent

if __name__ == '__main__':
    task = 'Read the CSV file sales.csv and sum the column revenue.'
    print('FINAL:', run_agent(task))

