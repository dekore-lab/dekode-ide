extends Node2D

@onready var player: Node2D = $Player
@export var speed: float = 200.0
@export var health: int = 100

signal player_died
signal score_changed(new_score: int)

const MAX_ENEMIES: int = 10
var enemies: Array = []
var score: int = 0

func _ready() -> void:
	print("Game started!")
	set_process(true)
	_spawn_enemies()

func _process(delta: float) -> void:
	if Input.is_action_pressed("move_right"):
		player.position.x += speed * delta
	elif Input.is_action_pressed("move_left"):
		player.position.x -= speed * delta

func _spawn_enemies() -> void:
	for i in range(MAX_ENEMIES):
		var enemy = preload("res://enemy.tscn").instantiate()
		enemy.position = Vector2(
			randf_range(0, 1280),
			randf_range(0, 720)
		)
		add_child(enemy)
		enemies.append(enemy)

func take_damage(amount: int) -> void:
	health = clamp(health - amount, 0, 100)
	if health <= 0:
		emit_signal("player_died")
		queue_free()

func add_score(points: int) -> void:
	score += points
	emit_signal("score_changed", score)
	print("Score: ", score)

func move() -> void:
    var speed: int = 100
    position += speed
    