#!/usr/bin/env python3

import torch as th
from torch.optim.optimizer import Optimizer, required


class MomentumIGT(Optimizer):

    """
    Implementation of Implicit Gradient Transport,
    and its Heavyball and Nesterov versions.

    Arguments:

        * params -- parameters to optimize
        * lr=required -- learning rate
        * momentum=0.0 -- momentum factor
        * dampening=0.0 -- momentum dampening factor
        * weight_decay=0.0 -- weight decay factor
        * nesterov=False -- wheter to use nesterov or not
        * delta=1.0 -- IGT's delta displacement parameter

    Example:

        # Training
        opt = MomentumIGT(model.parameters(), lr=0.1, momentum=0.9)
        opt.train() # Optional if opt.eval() is never used
        error = loss(model(X), y)
        error.backward()
        opt.step()

        # Evaluation
        model.eval()
        opt.eval()
        error = loss(model(X), y)

    Notes:

        * This implementation requires 5 copies of the model's parameters.
          (igt_velocity, momentum_velocity, true_params, gradients, params)
          I think it's possible to have a version with only 4 copies,
          but it would sacrifice some clarity.
        * Heavyball and Nesterov are implemented as in PyTorch's SGD.
    """

    def __init__(self, params, lr=required, momentum=0.0, dampening=0.0,
                 weight_decay=0.0, nesterov=False, delta=1.0):

        if weight_decay < 0.0:
            msg = "Invalid weight_decay value: {}".format(weight_decay)
            raise ValueError(msg)

        if delta <= 0.0:
            raise ValueError("Invalid delta value: {}".format(delta))

        defaults = {
                'lr': lr,
                'momentum': momentum,
                'dampening': dampening,
                'weight_decay': weight_decay,
                'nesterov': nesterov,
                'delta': delta,
                'num_steps': 0,
                'transported': True,
        }
        if nesterov and (momentum <= 0 or dampening != 0):
            msg = "Nesterov momentum requires a momentum and zero dampening"
            raise ValueError(msg)

        super(MomentumIGT, self).__init__(params, defaults)


    def __setstate__(self, state):
        super(MomentumIGT, self).__setstate__(state)
        for group in self.param_groups:
            group.setdefault('nesterov', False)


    def step(self, closure=None):

        loss = None
        if closure is not None:
            loss = closure()

        for group in self.param_groups:
            lr = group['lr']
            momentum = group['momentum']
            dampening = group['dampening']
            weight_decay = group['weight_decay']
            nesterov = group['nesterov']
            delta = group['delta']
            num_steps = group['num_steps']

            gamma = num_steps / (num_steps + delta)
            future_gamma = (num_steps + 1) / (num_steps + 1 + delta)
            future_transport = future_gamma / (1.0 - future_gamma)

            for p in group['params']:

                if p.grad is None:
                    continue

                d_p = p.grad.data  # Transported gradients
                param_state = self.state[p]

                # Apply weight decay
                if weight_decay != 0:
                    d_p.add_(weight_decay, p.data)

                # Init buffers appropriately
                if num_steps == 0:

                    if 'igt_velocity' not in param_state:
                        param_state['igt_velocity'] = th.zeros_like(p.data)
                    if 'true_params' not in param_state:
                        param_state['true_params'] = p.data.clone()
                    if momentum != 0 and 'momentum_velocity' not in param_state:
                        param_state['momentum_velocity'] = th.zeros_like(p.data)

                    igt_velocity = param_state['igt_velocity']
                    true_params = param_state['true_params']

                    # Compute first step and initial values
                    igt_velocity.add_(d_p)
                    if momentum != 0:
                        param_state['momentum_velocity'].add_(igt_velocity)

                # Compute the IGT update
                else:
                    igt_velocity = param_state['igt_velocity']
                    true_params = param_state['true_params']

                    # Update IGT's velocity
                    igt_velocity.mul_(gamma).add_((1.0 - gamma), d_p)

                    # Compute momentum if necessary
                    if momentum != 0:
                        momentum_velocity = param_state['momentum_velocity']
                        momentum_velocity.mul_(momentum).add_(1.0 - dampening,
                                                              igt_velocity)

                # Update true and transported parameters
                if momentum == 0:
                    # Update true parameters
                    true_params.add_(-lr, igt_velocity)

                    # Set parameters to transported ones
                    p.data.copy_(true_params)
                    p.data.add_(-lr * future_transport, igt_velocity)
                else:
                    momentum_velocity = param_state['momentum_velocity']
                    if nesterov:
                        true_params.add_(-lr, igt_velocity)
                        true_params.add_(-lr * momentum, momentum_velocity)

                        # Set parameters to transported ones
                        p.data.copy_(true_params)
                        p.data.add_(-lr * future_transport, igt_velocity)
                        p.data.add_(-lr * momentum * future_transport,
                                    momentum_velocity)
                    else:
                        true_params.add_(-lr, momentum_velocity)

                        # Set parameters to transported ones
                        p.data.copy_(true_params)
                        p.data.add_(-lr * future_transport, momentum_velocity)

            group['num_steps'] = num_steps + 1
        return loss

    def train(self):
        """
        Swaps true and transported parameters.

        Useful for switching from inference to training.
        """
        for group in self.param_groups:
            lr = group['lr']
            momentum = group['momentum']
            nesterov = group['nesterov']
            delta = group['delta']
            num_steps = group['num_steps']
            transported = group['transported']

            gamma = (num_steps) / (num_steps + delta)
            transport = gamma / (1.0 - gamma)

            if not transported and num_steps > 0:
                for p in group['params']:
                    # Should compute the future transported params
                    param_state = self.state[p]
                    igt_velocity = param_state['igt_velocity']
                    true_params = param_state['true_params']
                    p.data.copy_(true_params)
                    if momentum == 0:
                        p.data.add_(-lr * transport, igt_velocity)
                    else:
                        momentum_velocity = param_state['momentum_velocity']
                        if nesterov:
                            p.data.add_(-lr * transport, igt_velocity)
                            p.data.add_(-lr * momentum * transport,
                                        momentum_velocity)
                        else:
                            p.data.add_(-lr * transport, momentum_velocity)
                group['transported'] = True

    def eval(self):
        for group in self.param_groups:
            if group['transported']:
                for p in group['params']:
                    # Copy true_params to the params
                    p.data.copy_(self.state[p]['true_params'])
                group['transported'] = False
