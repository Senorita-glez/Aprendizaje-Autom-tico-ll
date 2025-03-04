import random
import torch
from torch import nn
import torch.nn.functional as F
from typing import Optional
from collections import deque
from pprint import pprint
import numpy as np

from typing import List, Optional, Set
from atarigon.api import Goshi, Goban, Ten
from atarigon.exceptions import (
    NotEnoughPlayersError,
    SmallBoardError,
    InvalidMoveError,
    HikūtenError, KūtenError,
)

class DQN(nn.Module):
    def __init__(self, in_states, h1_nodes, out_actions):
        super().__init__()

        self.fc1 = nn.Linear(in_states, h1_nodes)
        self.fc2 = nn.Linear(h1_nodes, h1_nodes)
        self.fc3 = nn.Linear(h1_nodes, h1_nodes)
        self.fc4 = nn.Linear(h1_nodes, h1_nodes)
        self.out = nn.Linear(h1_nodes, out_actions)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        x = F.relu(self.fc4(x))
        x = self.out(x)
        return x
    
class ReplayMemory():
    def __init__(self, maxlen):
        self.memory = deque([], maxlen=maxlen)

    def append(self, transition):
        self.memory.append(transition)

    def sample(self, sample_size):
        return random.sample(self.memory, sample_size)
    
    def __len__(self):
        return len(self.memory)

class TronGoshi(Goshi):
    """
    A player that hopes for the best.

    On the other side of the screen, it looks so easy.
    """

    around_points = [Ten(0, 1), Ten(1, 0), Ten(0, -1), Ten(-1, 0)]

    # Hiperparámetros
    learning_rate_a = 0.001
    discount_factor_g = 0.8
    min_epsilon_value = 0.6
    epsilon_decae = 0.995
    network_sync_rate = 100
    replay_memory_size = 10000
    mini_batch_size = 64

    # Cosas de la red neuronal
    loss_fn = nn.MSELoss()
    goban_size = 19
    num_states = goban_size*goban_size
    num_actions = goban_size*goban_size

    epsilon = 1
    memory = ReplayMemory(replay_memory_size)

    # Creamos las redes de política y objetivo
    policy_dqn = DQN(in_states=num_states, h1_nodes= num_states*2, out_actions = num_actions)
    target_dqn = DQN(in_states=num_states, h1_nodes= num_states*2, out_actions = num_actions)

    #policy_dqn.load_state_dict(torch.load('tron_dql.pt', map_location=torch.device('cpu'), weights_only=True))
    target_dqn.load_state_dict(policy_dqn.state_dict())

    optimizer = torch.optim.Adam(policy_dqn.parameters())

    # Para actualizar la red objetivo
    step_count = 0
    illegal_moves = 0
    historical_reward = []
    total_reward = 0

    def __init__(self):
        """Initializes the player with the given name."""
        super().__init__(f'Evil_Tron')        

    def decide(self, goban: 'Goban') -> Optional[Ten]:
        """Gets a random empty position in the board.

        :param goban: The current observation of the game.
        :return: The next move as a (row, col) tuple.
        """

        state = self.goban_to_state(goban)

        # Finds all the empty positions in the observation
        empty_positions = [
            Ten(row, col)
            for row in range(len(goban.ban))
            for col in range(len(goban.ban[row]))
            if goban.ban[row][col] is None
        ]

        # Chooses a random valid empty position
        random.shuffle(empty_positions)
        for ten in empty_positions:
            if goban.ban[ten.row][ten.col] is None:
                random_position = ten.row*self.goban_size + ten.col
                break

        heuristic = self.get_move_heuristic(goban)
        if heuristic:
            h_row, h_col = heuristic
            heuristic = h_row * self.goban_size + h_col
        # Use an offensive euristic rule
        #print(heuristic)

        # Action es un valor de 0 a n, con n el total de casillas en el tablero
        # Al final se convierte a una coordenada del tablero con la clase Ten.
        if random.random() < self.epsilon:
            if heuristic and (random.random() < 0.5):
                action = heuristic
            else:
                action = random_position
        else:
            action = self.policy_dqn(torch.from_numpy(state).float()).argmax().item()

        ten_action = self.output_to_ten(action)
        new_state = self.get_new_state(goban, action)

        # Añadir la condición si es el único jugador, se terminó
        try:
            terminated = not goban.seichō(ten_action, self) 
        except:
            #self.illegal_moves += 1
            #print(f"Movimiento ilegal: {self.output_to_ten(action)} | Illegal moves: {self.illegal_moves}")
            #goban.print_board()
            terminated = True
        
        reward = self.reward_function(goban, action) if not terminated else -1000 # Penalización por haberse suicidado/colocar mal una pieza
        self.memory.append((state, action, new_state, reward, terminated))
        self.step_count+=1
        #print(f'Reward: {reward}')
        
        if len(self.memory) > self.mini_batch_size: # Revisar si también debería de ponerse la condicional de haber obtenido al menos alguna recompensa
            mini_batch = self.memory.sample(self.mini_batch_size)
            self.optimize(mini_batch)

            # Decaemiento de épsilon
            if self.epsilon > self.min_epsilon_value:
                self.epsilon = self.epsilon*self.epsilon_decae

            # Copia de la red de política después de tantos pasos
            if self.step_count > self.network_sync_rate:
                self.target_dqn.load_state_dict(self.policy_dqn.state_dict())
                self.step_count = 0

        action = self.output_to_ten(action)
        #try:
        #    goban.seichō(action, self)
        #except:
        #    action = None
        return action # Tiene que regresar un Ten, pero revisar para las otras funciones

    # Actualización de q values
    def optimize(self, mini_batch):
        current_q_list = []
        target_q_list = []

        for state, action, new_state, reward, terminated in mini_batch:
            state = torch.from_numpy(state).float()  # Convert state to float tensor
            new_state = torch.from_numpy(new_state).float()  # Convert new_state to float tensor
            
            if terminated:
                # El jugador terminó el juego, ya sea por suicidio, colocar mal una pieza o ser el último
                # Si terminó, el q value objetivo debe ser igual a la recompensa
                target = torch.FloatTensor([reward])
            else:
                # Calculamos el valor q
                with torch.no_grad():
                    target = torch.FloatTensor(
                        reward + self.discount_factor_g * self.target_dqn(new_state).max()
                    )

            # Obtenemos los valores q de la política
            current_q = self.policy_dqn(state)
            current_q_list.append(current_q)

            # Obtenemos los valos q del objetivo
            target_q = self.target_dqn(state)
            # Ajuste de la acción al objetivo que se calculó
            target_q[action] = target
            target_q_list.append(target_q)

        loss = self.loss_fn(torch.stack(current_q_list), torch.stack(target_q_list))
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        #torch.save(self.policy_dqn.state_dict(), "tron_dql.pt")

    # Función de recompensa
    def reward_function(self, goban: 'Goban', action):
        reward = 1.0
        if False: #self.did_we_win(goban):
            reward += 100
        # Si se terminó el juego
        # Si no
        else:
            # Si comimos alguna piedra(s)
            captured_stones = self.captured_stones(goban, action) 
            #print(f'Stones:{captured_stones}')
            if captured_stones:
                reward += 100*len(captured_stones)
            # Si defendimos alguna de nuestras fichas
            # Si se fortaleció alguna estructura
            # Si se colocó una pieza donde no
            # Si hubo suicidio

        return reward
    
    def captured_stones(self, goban: 'Goban', action):
        player = goban.stone_colors[self]
        action = self.output_to_ten(action)
        board = self.goban_to_numpy(goban)
        row, col = action

        board[row][col] = player
        captured = self.check_captures(board, action, player)

        return captured
    
    def check_captures(self,board, ten: Ten, player) -> Set[Goshi]:
        """Checks whether the group at the given position has liberties.

        :param ten: The position to check.
        :param goshi: The player making the move.
        :return: A set with the players that were captured. If no players
            were captured, the set is empty.
        """
        captured = set()
        for neighbor_coords in self.neighbourhood(ten):
            neighbor_player = board[neighbor_coords.row][neighbor_coords.col]
            if neighbor_player is None:
                # If the neighbor is empty, we don't capture anything
                continue
            if neighbor_player == player:
                # If the neighbor is us, we don't capture anything
                continue
            if self.player_liberties(board, neighbor_coords):
                # The neighbor has liberties, we don't capture it
                continue

            # The neighbour has no liberties, so we capture it
            captured.add(neighbor_player)
        return captured
    
    def stones_captured(self, board, ten: Ten):
        """Captures the group at the given position.

        Inthe end, that means removing all the stones of the group from
        the board, so we simply set all the intersections of the player
        to None.

        :param ten: The position to check.
        :raises HikūtenError: If the intersection is empty.
        """
        enemy = board[ten.row][ten.col]
        if enemy == 0:
            raise HikūtenError(ten)

        for r, row in enumerate(board):
            for c, player_in_coords in enumerate(row):
                if player_in_coords == enemy:
                    board[r][c] = 0
    
    def player_liberties(self, board, ten: Ten) -> Set[Ten]:
        """Liberties of the group for the player at the given position.

        :param ten: The position to check.
        :return: A list with the liberties of the group.
        """
        player = board[ten.row][ten.col]
        if player == 0:
            return True

        stack = [ten]
        visited = set()
        liberties = set()
        while stack:
            ten = stack.pop()
            if ten in visited:
                # Intersection already visited, so we skip it
                continue
            else:
                visited.add(ten)

            for neighbor_coords in self.neighbourhood(ten):
                neighbor_player = board[neighbor_coords.row][neighbor_coords.col]
                if neighbor_player == 0:
                    # Empty intersection in the neighbourhood, so we add it
                    liberties.add(neighbor_coords)
                elif neighbor_player == player:
                    # Our stone in the neighbourhood; we add it to the stack
                    stack.append(neighbor_coords)

        return liberties
    
    def neighbourhood(self, ten: Ten) -> List[Ten]:
        """The neighbourhood of a given intersecion.

        :param ten: The intersecion to check.
        :return: All the intersections belonging to the neighbourhood.
        """
        return [
            ten
            for ten in (ten + around for around in self.around_points)
            if self.is_inside_board(ten)
        ]
    
    def is_inside_board(self, ten: Ten) -> bool:
        """If the position is inside the board

        :param ten: The position to check.
        :return: True if the position is valid, False otherwise.
        """
        return 0 <= ten.row < self.goban_size and 0 <= ten.col < self.goban_size
    
    def get_move_heuristic(self, goban):
        weak_points = self.find_weak_points(goban)
        #pprint(weak_points)
        for key, value in weak_points.items():
            if value:
                for key1, value1 in value.items():
                    return value1[0]

    def find_weak_points(self, goban: 'Goban'):
        board = self.goban_to_numpy(goban)
        enemies = [goban.stone_colors[p] for p in goban.stone_colors if p!=self]
        priority = {
            1: {},
            2: {},
            3: {},
            4: {}
        }
        for enemy in enemies:
            visited = []
            for i, row in enumerate(board):
                for j, cell in enumerate(row):
                    if (i,j) in visited:
                        continue
                    if cell == enemy:
                        tl, points_of_interest, visited, stones_in_danger = self.check_liberties(board, enemy, (i,j))
                        if tl not in priority:
                            priority[tl] = {}
                        if stones_in_danger not in priority[tl]:
                            priority[tl][stones_in_danger] = []
                        try:
                            priority[tl][stones_in_danger] = points_of_interest
                        except:
                            priority[tl][stones_in_danger].append(points_of_interest)

        return priority
    
    def check_liberties(self, board, enemy, cell):
        around_points = [(1,0), (0,1), (-1,0), (0,-1)]
        total_liberties = 0
        points_of_interest = []
        to_check = []
        to_check.append(cell)
        visited = []
        dangered_stones = 1
        while to_check:
            point = to_check.pop(0)
            visited.append(point)
            for around_point in around_points:
                new_point = tuple(map(lambda i, j: i + j, point, around_point))
                row, col  = new_point
                if 0 <= row < len(board[0]) and 0 <= col < len(board[0]):
                    # is another enemy's stone
                    if board[row][col] == enemy:
                        if (row,col) not in visited:
                            dangered_stones += 1
                            to_check.append((row,col))
                        continue                    
                    # is empty
                    elif board[row][col] == 0:
                        total_liberties += 1
                        points_of_interest.append((row,col))
        return total_liberties, points_of_interest, visited, dangered_stones

    def did_we_win(self, goban: 'Goban'):
        for row in goban.ban:
            for player in row:
                if player!= self: 
                    return False
        return True
    
    def get_new_state(self, goban: 'Goban', action):
        board = self.goban_to_state(goban)
        board[action] = 1
        return board

    # Pasamos del formato de ban de goban a uno donde sólo haya números
    def goban_to_state(self, goban: 'Goban'):
        board = np.zeros(goban.size*goban.size)
        for i, row in enumerate(goban.ban):
            for j, p in enumerate(row):
                if p is not None:
                    board[i*goban.size + j] = 1 if p == self else -1 # TODO: Revisar aquí para cuando haya más jugadores
        return board
    
    def goban_to_numpy(self, goban: 'Goban'):
        board = np.zeros_like(goban.ban)
        for i, row in enumerate(goban.ban):
            for j, p in enumerate(row):
                if p is not None:
                    board[i,j] = goban.stone_colors[p]
        return board

    # Salida a coordenadas del tablero
    def output_to_ten(self, output):
        row, column = divmod(output, self.goban_size) 
        return Ten(row, column)